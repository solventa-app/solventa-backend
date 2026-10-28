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
from app.application.cifrado import cifrador_desde_entorno
from app.application.cliente_acl import ClienteAcl
from app.application.configuracion import Configuracion
from app.application.disparador import (
    BusConReintento,
    PlanificadorCloudTasks,
    PlanificadorNulo,
    PuertoPlanificador,
)
from app.application.reintento import ReintentoCobrosPendientes
from app.application.repositorio_cobros import RepositorioCobrosPostgres, construir_pool
from app.application.repositorio_sesiones import RepositorioSesionesPostgres
from app.application.servicio_cobros import (
    IntentoCobroEnConflicto,
    NoReintentable,
    ServicioCobros,
)
from app.application.servicio_sesiones import ServicioSesionesPago, SolicitudSesionInvalida
from app.observabilidad import configurar_trazas

# Sin esto, los logs INFO de app.application.servicio_cobros/bus_eventos/reintento
# se descartan en silencio (mismo motivo documentado en acl-worker/app/main.py).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("payments", instrumentos=("httpx", "psycopg"))


class OrdenCobroRequest(BaseModel):
    # Lo genera quien ordena el cobro y lo reenvía igual al repetir (CA-W05-04).
    id_intento_cobro: str = Field(min_length=1)
    poliza_id: str = Field(min_length=1)
    # El usuario CLIENTE de AUTH a quien se le cobra. Solo el id: el nombre vive en AUTH.
    cliente_id: str | None = Field(default=None, min_length=1)
    monto: str = Field(min_length=1)
    moneda: str = Field(min_length=1)
    token_medio_pago: str = Field(min_length=1)


class ErrorResponse(BaseModel):
    detail: str


class MedioPagoResponse(BaseModel):
    """Solo la marca y el tipo: nunca el token, el número ni los últimos dígitos (W05-A2)."""

    marca: str
    tipo: str


class CobroResponse(BaseModel):
    id: str
    id_intento_cobro: str
    poliza_id: str
    cliente_id: str | None = None
    monto: str
    moneda: str
    estado: str
    referencia: str | None = None
    motivo: str | None = None
    intentos: int
    # Reintentos manuales de un rechazado ya hechos y los que quedan (CA-W05-02).
    reintentos_rechazo: int = 0
    reintentos_rechazo_restantes: int = 0
    # W05-A3 y A5: INTERACTIVO (página de la pasarela) o RECURRENTE, y el medio tokenizado.
    origen: str = "RECURRENTE"
    medio: MedioPagoResponse | None = None
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None


class SolicitudSesionPagoRequest(BaseModel):
    """La envía POLICY cuando el titular pulsa «Pagar» (HU-W03, W03-28)."""

    propuesta_id: str = Field(min_length=1)
    cliente_id: str | None = Field(default=None, min_length=1)
    monto: str = Field(min_length=1)
    moneda: str = Field(min_length=1)
    descripcion: str = Field(min_length=1, max_length=120)
    url_retorno: str = Field(min_length=1)
    idioma: str = "es"


class ConfirmarSesionRequest(BaseModel):
    # La sesión debe ser de esta propuesta: si no, 404 igual que una inexistente.
    propuesta_id: str = Field(min_length=1)


class SesionPagoResponse(BaseModel):
    """`estado`: abierta, cobrada, rechazada, fallida, vencida o no_disponible. `motivo` dice qué
    hacer (p. ej. `pasarela_no_disponible`: reintentar; `requiere_otra_tarjeta`: al 3.er fallo
    seguido). `sesion_id` es el id de la pasarela, el que vuelve en la URL de regreso."""

    sesion_id: str | None = None
    propuesta_id: str
    intento: int
    id_intento_cobro: str
    estado: str
    motivo: str | None = None
    url_pago: str | None = None
    vence_en: datetime | None = None
    fallos_seguidos: int = 0
    cobro: CobroResponse | None = None


EstadoCobro = Literal["cobrando", "cobrado", "rechazado", "pendiente"]


class FiltroCobros(BaseModel):
    """Filtros de la búsqueda. Van en el CUERPO, no en la URL: las URL quedan en los
    logs de acceso, y la póliza identifica indirectamente a una persona (regla #6)."""

    poliza_id: str | None = None
    estado: EstadoCobro | None = None
    pagina: int | None = None
    tamano: int | None = None


class BalanceResponse(BaseModel):
    poliza_id: str
    balance: str


def _planificador(config: Configuracion) -> PuertoPlanificador:
    if config.disparador != "externo":
        return PlanificadorNulo()
    return PlanificadorCloudTasks(
        config.tareas_proyecto,
        config.tareas_region,
        config.tareas_cola,
        config.url_propia,
        config.tareas_cuenta,
    )


class PasadaResponse(BaseModel):
    procesados: int
    por_reintentar: int


def create_app(
    config: Configuracion | None = None,
    servicio_cobros: ServicioCobros | None = None,
    reintento: ReintentoCobrosPendientes | None = None,
    servicio_sesiones: ServicioSesionesPago | None = None,
    medios=None,
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
            app.state.servicio_sesiones = servicio_sesiones
            app.state.medios = medios
        else:
            pool = construir_pool(config.database_url)
            repositorio = RepositorioCobrosPostgres(pool, cifrador_desde_entorno())
            cliente_acl = ClienteAcl(
                config.acl_url,
                config.acl_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "acl-worker"),
            )
            planificador = _planificador(config)
            bus = BusEventosLog()
            if config.disparador == "externo":
                # Un cobro que queda `pendiente` programa su reintento en Cloud Tasks.
                bus = BusConReintento(bus, planificador, config.reintento_intervalo_segundos)
            app.state.servicio_cobros = ServicioCobros(
                repositorio, cliente_acl, bus, config.reintentos_rechazo_maximo
            )
            repositorio_sesiones = RepositorioSesionesPostgres(pool, cifrador_desde_entorno())
            app.state.medios = repositorio_sesiones.medios
            app.state.servicio_sesiones = ServicioSesionesPago(
                repositorio_sesiones, cliente_acl, bus, repositorio.obtener
            )
            app.state.reintento = ReintentoCobrosPendientes(
                repositorio,
                cliente_acl,
                bus,
                config.reintento_intervalo_segundos,
                config.reintento_max_intentos,
                planificador,
                config.minutos_cobro_atascado,
            )

        # Con DISPARADOR=externo no hay bucle: Cloud Tasks llama a POST /cobros/_reintentar.
        if app.state.reintento is not None and config.disparador == "bucle":
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

    def _medios(cobros: list[dict]) -> dict[str, dict]:
        ids = sorted({c["medio_pago_id"] for c in cobros if c.get("medio_pago_id")})
        return app.state.medios(ids) if ids and app.state.medios else {}

    def _respuesta(cobro: dict, medios: dict[str, dict] | None = None) -> CobroResponse:
        restantes = app.state.servicio_cobros.reintentos_restantes(cobro)
        medios = _medios([cobro]) if medios is None else medios
        medio = medios.get(cobro.get("medio_pago_id") or "")
        datos = {k: v for k, v in cobro.items() if k != "medio"}
        return CobroResponse(**datos, reintentos_rechazo_restantes=restantes, medio=medio)

    def _sesion(datos: dict) -> SesionPagoResponse:
        cobro = datos.get("cobro")
        return SesionPagoResponse(
            **{k: v for k, v in datos.items() if k not in ("cobro", "id", "proveedor_sesion_id")},
            sesion_id=datos.get("proveedor_sesion_id"),
            cobro=_respuesta(cobro) if cobro else None,
        )

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
        except IntentoCobroEnConflicto as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _respuesta(cobro)

    @app.post("/cobros/_reintentar", tags=["operacion"], response_model=PasadaResponse)
    def reintentar_pendientes() -> PasadaResponse:
        """La llama Cloud Tasks (`DISPARADOR=externo`): una pasada del reintento de cobros
        `pendiente` y de los atascados en `cobrando`. Es privada (IAM de Cloud Run) y nunca
        responde 5xx por la pasarela: lo que no se pudo reintentar sigue `pendiente`."""
        reintento = app.state.reintento
        if reintento is None:
            raise HTTPException(status_code=404, detail="reintento no disponible")
        return PasadaResponse(**reintento.una_pasada_externa())

    @app.post("/cobros/buscar", tags=["cobros"], response_model=list[CobroResponse])
    def buscar_cobros(filtro: FiltroCobros) -> list[CobroResponse]:
        """Tabla del back-office (CA-W05-01): filtros opcionales por póliza y estado."""
        cobros = app.state.servicio_cobros.listar(
            filtro.poliza_id, filtro.estado, filtro.pagina, filtro.tamano
        )
        medios = _medios(cobros)  # una sola consulta para toda la página
        return [_respuesta(c, medios) for c in cobros]

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

    # ---------- Página de pago alojada de la pasarela (HU-W03, W03-28 a W03-30) ----------

    @app.post(
        "/sesiones-pago",
        tags=["sesiones"],
        response_model=SesionPagoResponse,
        responses={422: {"model": ErrorResponse, "description": "la pasarela no la aceptó"}},
    )
    def abrir_sesion(body: SolicitudSesionPagoRequest) -> SesionPagoResponse:
        """Abre la sesión de pago de la propuesta o devuelve la vigente. Concilia la anterior antes
        de abrir otra, así que nunca cobra dos veces. Nunca un 5xx por la pasarela: sin respuesta,
        `estado` no_disponible con el motivo."""
        try:
            return _sesion(app.state.servicio_sesiones.abrir(body.model_dump()))
        except SolicitudSesionInvalida as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/sesiones-pago/{sesion_id}/confirmar",
        tags=["sesiones"],
        response_model=SesionPagoResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def confirmar_sesion(sesion_id: str, body: ConfirmarSesionRequest) -> SesionPagoResponse:
        """Al regresar de la pasarela (`sesion_id` es el de la URL de regreso). Confirma con la
        pasarela de servidor a servidor; si se cobró, devuelve el cobro. Idempotente."""
        datos = app.state.servicio_sesiones.confirmar(sesion_id, body.propuesta_id)
        if datos is None:
            raise HTTPException(status_code=404, detail="sesión no encontrada")
        return _sesion(datos)

    @app.get(
        "/propuestas/{propuesta_id}/sesion-pago",
        tags=["sesiones"],
        response_model=SesionPagoResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def ultima_sesion(propuesta_id: str) -> SesionPagoResponse:
        """La última sesión de la propuesta, sin preguntarle a la pasarela."""
        datos = app.state.servicio_sesiones.ultima(propuesta_id)
        if datos is None:
            raise HTTPException(status_code=404, detail="sin sesión de pago")
        return _sesion(datos)

    @app.get("/polizas/{poliza_id}/balance", tags=["cobros"], response_model=BalanceResponse)
    def balance(poliza_id: str) -> BalanceResponse:
        total = app.state.servicio_cobros.balance_poliza(poliza_id)
        return BalanceResponse(poliza_id=poliza_id, balance=total)

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
