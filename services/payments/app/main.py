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
from pydantic import AliasChoices, BaseModel, Field

from app.application.bus_eventos import BusEventosLog
from app.application.cliente_acl import ClienteAcl
from app.application.configuracion import Configuracion
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
    # Transición (expandir/contraer): se acepta también el nombre anterior.
    id_intento_cobro: str = Field(
        min_length=1, validation_alias=AliasChoices("id_intento_cobro", "clave_idempotencia")
    )
    poliza_id: str = Field(min_length=1)
    monto: str = Field(min_length=1)
    moneda: str = Field(min_length=1)
    token_medio_pago: str = Field(min_length=1)


class ErrorResponse(BaseModel):
    detail: str


class CobroResponse(BaseModel):
    id: str
    id_intento_cobro: str
    poliza_id: str
    monto: str
    moneda: str
    estado: str
    referencia: str | None = None
    motivo: str | None = None
    intentos: int
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
        else:
            pool = construir_pool(config.database_url)
            repositorio = RepositorioCobrosPostgres(pool)
            cliente_acl = ClienteAcl(config.acl_url, config.acl_timeout_segundos)
            bus = BusEventosLog()
            app.state.servicio_cobros = ServicioCobros(repositorio, cliente_acl, bus)
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
        return CobroResponse(**cobro)

    @app.post("/cobros/buscar", tags=["cobros"], response_model=list[CobroResponse])
    def buscar_cobros(filtro: FiltroCobros) -> list[CobroResponse]:
        """Tabla del back-office (CA-W05-01): filtros opcionales por póliza y estado."""
        cobros = app.state.servicio_cobros.listar(filtro.poliza_id, filtro.estado)
        return [CobroResponse(**c) for c in cobros]

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
        return CobroResponse(**cobro)

    @app.post(
        "/cobros/{cobro_id}/reintentar",
        tags=["cobros"],
        response_model=CobroResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    def reintentar_cobro(cobro_id: str) -> CobroResponse:
        """CA-W05-02: concilia con la pasarela y, si hace falta, cobra con la misma clave.
        Nunca un 5xx por la pasarela: si no responde, el cobro sigue `pendiente`."""
        try:
            cobro = app.state.servicio_cobros.reintentar(cobro_id)
        except NoReintentable as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if cobro is None:
            raise HTTPException(status_code=404, detail="cobro no encontrado")
        return CobroResponse(**cobro)

    @app.get("/polizas/{poliza_id}/balance", tags=["cobros"], response_model=BalanceResponse)
    def balance(poliza_id: str) -> BalanceResponse:
        total = app.state.servicio_cobros.balance_poliza(poliza_id)
        return BalanceResponse(poliza_id=poliza_id, balance=total)

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
