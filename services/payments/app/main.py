"""PAYMENTS: cobros con pasarela tokenizada (HU-W05).

Único escritor de la tabla `cobros` (regla de arquitectura #1). Habla con la
pasarela SOLO a través del ACL Worker (regla de arquitectura #2); nunca la
llama directamente ni guarda estado del circuito — eso vive en `acl-worker`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from app.application.autenticacion_servicios import autenticacion_para
from app.application.bus_eventos import BusEventosLog
from app.application.cifrado import CifradorAesGcm
from app.application.cliente_acl import ClienteAcl
from app.application.configuracion import Configuracion
from app.application.medios_pago import (
    MedioPagoInvalido,
    MedioPagoNoUtilizable,
    RepositorioMediosPagoPostgres,
    ServicioMediosPago,
)
from app.application.reintento import ReintentoCobrosPendientes
from app.application.repositorio_cobros import RepositorioCobrosPostgres, construir_pool
from app.application.servicio_cobros import (
    IntentoCobroEnConflicto,
    NoReintentable,
    ServicioCobros,
)

# Sin esto, los logs INFO de app.application.servicio_cobros/bus_eventos/reintento
# se descartan en silencio (mismo motivo documentado en acl-worker/app/main.py).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class OrdenCobroRequest(BaseModel):
    # Lo genera quien ordena el cobro y lo reenvía igual al repetir (CA-W05-04).
    id_intento_cobro: str = Field(min_length=1)
    poliza_id: str = Field(min_length=1)
    # El usuario CLIENTE de AUTH a quien se le cobra. Solo el id: el nombre vive en AUTH.
    cliente_id: str | None = Field(default=None, min_length=1)
    monto: str = Field(min_length=1)
    moneda: str = Field(min_length=1)
    # Hay que indicar UNO de los dos: el token suelto del proveedor, o un medio de pago ya
    # registrado (que debe ser del `cliente_id`). Nunca datos de tarjeta.
    token_medio_pago: str | None = Field(default=None, min_length=1)
    medio_pago_id: str | None = Field(default=None, min_length=1)


class SolicitudMedioPago(BaseModel):
    """Lo que llega tras tokenizar en la pasarela: el token y lo mostrable. Nunca la tarjeta."""

    cliente_id: str = Field(min_length=1)
    token: str = Field(min_length=1)
    marca: str = Field(min_length=1)
    ultimos4: str = Field(min_length=4, max_length=4)
    vencimiento_mes: int
    vencimiento_anio: int


class MedioPagoResponse(BaseModel):
    """El token nunca sale de PAYMENTS."""

    id: str
    cliente_id: str
    marca: str
    ultimos4: str
    vencimiento_mes: int
    vencimiento_anio: int
    creado_en: datetime | None = None


class MedioPagoResumen(BaseModel):
    marca: str
    ultimos4: str


class ErrorResponse(BaseModel):
    detail: str


class CobroResponse(BaseModel):
    id: str
    id_intento_cobro: str
    poliza_id: str
    cliente_id: str | None = None
    # Con qué tarjeta se cobró (solo marca y últimos 4); nulo si fue con un token suelto.
    medio_pago: MedioPagoResumen | None = None
    monto: str
    moneda: str
    estado: str
    referencia: str | None = None
    motivo: str | None = None
    intentos: int
    # Reintentos manuales de un rechazado ya hechos y los que quedan (CA-W05-02).
    reintentos_rechazo: int = 0
    reintentos_rechazo_restantes: int = 0
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None


EstadoCobro = Literal["cobrando", "cobrado", "rechazado", "pendiente"]


class FiltroCobros(BaseModel):
    """Filtros de la búsqueda. Van en el CUERPO, no en la URL: las URL quedan en los
    logs de acceso, y la póliza identifica indirectamente a una persona (regla #6)."""

    poliza_id: str | None = None
    estado: EstadoCobro | None = None


class BalanceResponse(BaseModel):
    poliza_id: str
    balance: str


def create_app(
    config: Configuracion | None = None,
    servicio_cobros: ServicioCobros | None = None,
    reintento: ReintentoCobrosPendientes | None = None,
    medios_pago: ServicioMediosPago | None = None,
) -> FastAPI:
    """`servicio_cobros`/`reintento` permiten inyectar dobles de prueba ya
    construidos sin levantar PostgreSQL ni el ACL Worker reales (ver
    `tests/fakes.py`). En producción (o al verificar en vivo con
    `docker compose`) se omiten y `create_app` construye todo desde
    `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: ConnectionPool | None = None
        cliente_acl: ClienteAcl | None = None

        if servicio_cobros is not None:
            app.state.servicio_cobros = servicio_cobros
            app.state.reintento = reintento
            app.state.medios_pago = medios_pago
        else:
            pool = construir_pool(config.database_url)
            repositorio = RepositorioCobrosPostgres(pool)
            app.state.medios_pago = ServicioMediosPago(
                RepositorioMediosPagoPostgres(pool),
                CifradorAesGcm.desde_base64(config.cifrado_clave),
            )
            cliente_acl = ClienteAcl(
                config.acl_url,
                config.acl_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "acl-worker"),
            )
            bus = BusEventosLog()
            app.state.servicio_cobros = ServicioCobros(
                repositorio,
                cliente_acl,
                bus,
                config.reintentos_rechazo_maximo,
                app.state.medios_pago,
            )
            app.state.reintento = ReintentoCobrosPendientes(
                repositorio,
                cliente_acl,
                bus,
                config.reintento_intervalo_segundos,
                config.reintento_max_intentos,
            )

        if app.state.reintento is not None:
            app.state.reintento.iniciar()

        try:
            yield
        finally:
            if app.state.reintento is not None:
                await app.state.reintento.detener()
            if cliente_acl is not None:
                cliente_acl.cerrar()
            if pool is not None:
                pool.close()

    app = FastAPI(title="solventa-payments", version="0.1.0", lifespan=lifespan)

    def _respuestas(cobros: list[dict]) -> list[CobroResponse]:
        """Una sola consulta para los medios de pago de todos los cobros de la lista."""
        medios = app.state.medios_pago
        resumenes = (
            medios.resumenes([c["medio_pago_id"] for c in cobros if c.get("medio_pago_id")])
            if medios is not None
            else {}
        )
        return [
            CobroResponse(
                **{k: v for k, v in c.items() if k != "medio_pago_id"},
                medio_pago=resumenes.get(c.get("medio_pago_id")),
                reintentos_rechazo_restantes=app.state.servicio_cobros.reintentos_restantes(c),
            )
            for c in cobros
        ]

    def _respuesta(cobro: dict) -> CobroResponse:
        return _respuestas([cobro])[0]

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "payments", "estado": "ok"}

    @app.post(
        "/cobros",
        tags=["cobros"],
        response_model=CobroResponse,
        responses={409: {"model": ErrorResponse, "description": "mismo intento, otros datos"}},
    )
    def crear_cobro(body: OrdenCobroRequest) -> CobroResponse:
        try:
            cobro = app.state.servicio_cobros.crear_cobro(body.model_dump())
        except (IntentoCobroEnConflicto, MedioPagoNoUtilizable) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _respuesta(cobro)

    @app.post("/cobros/buscar", tags=["cobros"], response_model=list[CobroResponse])
    def buscar_cobros(filtro: FiltroCobros) -> list[CobroResponse]:
        """Tabla del back-office (CA-W05-01): filtros opcionales por póliza y estado."""
        cobros = app.state.servicio_cobros.listar(filtro.poliza_id, filtro.estado)
        return _respuestas(cobros)

    @app.post(
        "/medios-pago",
        tags=["medios de pago"],
        response_model=MedioPagoResponse,
        responses={400: {"model": ErrorResponse}},
    )
    def registrar_medio_pago(body: SolicitudMedioPago) -> MedioPagoResponse:
        """Registra la tarjeta ya tokenizada por la pasarela. Idempotente. Solo el token y lo
        mostrable: jamás el número de la tarjeta (regla #6). Interno: el BFF no lo expone."""
        try:
            medio = app.state.medios_pago.registrar(
                body.cliente_id,
                body.token,
                body.marca,
                body.ultimos4,
                body.vencimiento_mes,
                body.vencimiento_anio,
            )
        except MedioPagoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return MedioPagoResponse(**medio)

    @app.get(
        "/clientes/{cliente_id}/medios-pago",
        tags=["medios de pago"],
        response_model=list[MedioPagoResponse],
    )
    def medios_pago_del_cliente(cliente_id: str) -> list[MedioPagoResponse]:
        return [MedioPagoResponse(**m) for m in app.state.medios_pago.listar_de_cliente(cliente_id)]

    @app.get(
        "/cobros/{cobro_id}",
        tags=["cobros"],
        response_model=CobroResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def obtener_cobro(cobro_id: str) -> CobroResponse:
        cobro = app.state.servicio_cobros.obtener(cobro_id)
        if cobro is None:
            raise HTTPException(status_code=404, detail="cobro no encontrado")
        return _respuesta(cobro)

    @app.post(
        "/cobros/{cobro_id}/reintentar",
        tags=["cobros"],
        response_model=CobroResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    def reintentar_cobro(cobro_id: str) -> CobroResponse:
        """CA-W05-02: un `pendiente` se concilia y, si hace falta, se cobra con la misma clave;
        un `rechazado` se vuelve a cobrar con una referencia nueva, hasta 3 veces (409 al
        agotarlas). Nunca un 5xx por la pasarela: si no responde, el cobro sigue `pendiente`."""
        try:
            cobro = app.state.servicio_cobros.reintentar(cobro_id)
        except NoReintentable as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if cobro is None:
            raise HTTPException(status_code=404, detail="cobro no encontrado")
        return _respuesta(cobro)

    @app.get("/polizas/{poliza_id}/balance", tags=["cobros"], response_model=BalanceResponse)
    def balance(poliza_id: str) -> BalanceResponse:
        total = app.state.servicio_cobros.balance_poliza(poliza_id)
        return BalanceResponse(poliza_id=poliza_id, balance=total)

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
