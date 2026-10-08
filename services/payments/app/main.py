"""PAYMENTS: cobros con pasarela tokenizada (HU-W05).

Único escritor de la tabla `cobros` (regla de arquitectura #1). Habla con la
pasarela SOLO a través del ACL Worker (regla de arquitectura #2); nunca la
llama directamente ni guarda estado del circuito — eso vive en `acl-worker`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from app.application.bus_eventos import BusEventosLog
from app.application.cliente_acl import ClienteAcl
from app.application.configuracion import Configuracion
from app.application.reintento import ReintentoCobrosPendientes
from app.application.repositorio_cobros import RepositorioCobrosPostgres, construir_pool
from app.application.servicio_cobros import ServicioCobros

# Sin esto, los logs INFO de app.application.servicio_cobros/bus_eventos/reintento
# se descartan en silencio (mismo motivo documentado en acl-worker/app/main.py).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class OrdenCobroRequest(BaseModel):
    clave_idempotencia: str
    poliza_id: str
    monto: str
    moneda: str
    token_medio_pago: str


class CobroResponse(BaseModel):
    id: str
    clave_idempotencia: str
    poliza_id: str
    monto: str
    moneda: str
    estado: str
    referencia: str | None = None
    motivo: str | None = None
    intentos: int


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
            repositorio.preparar_esquema()
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

    @app.post("/cobros", tags=["cobros"], response_model=CobroResponse)
    def crear_cobro(body: OrdenCobroRequest) -> CobroResponse:
        cobro = app.state.servicio_cobros.crear_cobro(body.model_dump())
        return CobroResponse(**cobro)

    @app.get("/polizas/{poliza_id}/balance", tags=["cobros"], response_model=BalanceResponse)
    def balance(poliza_id: str) -> BalanceResponse:
        total = app.state.servicio_cobros.balance_poliza(poliza_id)
        return BalanceResponse(poliza_id=poliza_id, balance=total)

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
