"""CLAIMS: escritor único de la mesa de siniestros (HU-W04).

Servicio privado: solo lo llama el BFF (IAM de Cloud Run). La autorización por rol (solo BACKOFFICE)
la hace el BFF antes de llamar (W04-11).
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from pymongo import MongoClient

from app.application.autenticacion_servicios import autenticacion_para
from app.application.bus_eventos import BusEventosLog
from app.application.cifrado import CifradorAesGcm
from app.application.clientes import ClientePayments, ClientePolicy
from app.application.configuracion import Configuracion
from app.application.repositorio import RepositorioSiniestrosMongo
from app.application.servicio_siniestros import (
    DependenciaNoDisponible,
    ErrorNegocio,
    ServicioSiniestros,
)
from app.observabilidad import configurar_trazas
from app.sembrar import sembrar

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx registra cada URL pedida en INFO, y las URLs firmadas del almacenamiento no deben quedar en
# los logs (como en POLICY).
logging.getLogger("httpx").setLevel(logging.WARNING)

configurar_trazas("claims", instrumentos=("httpx", "pymongo"))


class EstadoSiniestro(StrEnum):
    PENDIENTE = "PENDIENTE"
    EN_REVISION = "EN_REVISION"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


class AprobarRequest(BaseModel):
    actor_usuario_id: str | None = None  # el BACKOFFICE que resuelve (id opaco de AUTH)


class RechazarRequest(BaseModel):
    motivo: str
    comentario: str | None = Field(default=None, max_length=2000)
    actor_usuario_id: str | None = None


class DerivarRequest(BaseModel):
    perito_id: str = Field(min_length=1)
    instrucciones: str | None = Field(default=None, max_length=2000)
    actor_usuario_id: str | None = None


def create_app(
    config: Configuracion | None = None,
    servicio: ServicioSiniestros | None = None,
    reiniciar: Callable[[], int] | None = None,
) -> FastAPI:
    """`servicio` y `reiniciar` permiten inyectar dobles de prueba sin levantar MongoDB (ver
    `tests/fakes.py`)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_mongo: MongoClient | None = None
        policy: ClientePolicy | None = None
        payments: ClientePayments | None = None
        app.state.reiniciar = reiniciar
        if servicio is not None:
            app.state.servicio = servicio
        else:
            cliente_mongo = MongoClient(config.mongo_uri, tz_aware=True)
            cifrador = CifradorAesGcm.desde_entorno(
                cifrado_clave=config.cifrado_clave,
                kms_key_name=config.kms_key_name,
                cifrado_clave_envuelta=config.cifrado_clave_envuelta,
            )
            repositorio = RepositorioSiniestrosMongo(
                cliente_mongo, cifrador, config.mongo_db, config.lectura_max_ms
            )
            modo = config.autenticacion_servicios
            policy = ClientePolicy(
                config.policy_url,
                config.servicios_timeout_segundos,
                autenticacion_para(modo, "policy"),
            )
            payments = ClientePayments(
                config.payments_url,
                config.servicios_timeout_segundos,
                autenticacion_para(modo, "payments"),
            )
            app.state.servicio = ServicioSiniestros(
                repositorio, policy=policy, payments=payments, bus=BusEventosLog()
            )
            if app.state.reiniciar is None:
                datos = Path(config.semilla_dir)
                app.state.reiniciar = lambda: sembrar(repositorio, datos)
        try:
            yield
        finally:
            for cliente in (policy, payments):
                if cliente is not None:
                    cliente.cerrar()
            if cliente_mongo is not None:
                cliente_mongo.close()

    app = FastAPI(title="solventa-claims", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "claims", "estado": "ok"}

    @app.get("/siniestros", tags=["siniestros"])
    def listar(estado: EstadoSiniestro | None = None) -> list[dict]:
        """Bandeja: del más reciente al más antiguo. Se lee de la réplica (HA-LAT-003)."""
        return app.state.servicio.listar(estado.value if estado else None)

    @app.get("/siniestros/{siniestro_id}", tags=["siniestros"])
    def obtener(siniestro_id: str) -> dict:
        expediente = app.state.servicio.obtener(siniestro_id)
        if expediente is None:
            raise HTTPException(status_code=404, detail="siniestro no encontrado")
        return expediente

    @app.get("/siniestros/{siniestro_id}/peritos", tags=["siniestros"])
    def peritos(siniestro_id: str) -> list[dict]:
        return app.state.servicio.peritos_para(siniestro_id)

    def _accion(accion) -> dict | JSONResponse:
        """Un error de negocio es 409 y una dependencia caída 503, siempre con un `detail` en
        lenguaje del operador que el BFF muestra tal cual (nunca un 5xx crudo al cliente)."""
        try:
            return accion()
        except ErrorNegocio as error:
            return JSONResponse(status_code=409, content={"detail": str(error)})
        except DependenciaNoDisponible as error:
            return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.post("/siniestros/{siniestro_id}/aprobar", tags=["resolucion"])
    def aprobar(siniestro_id: str, body: AprobarRequest) -> dict:
        """Registra la orden de pago en PAYMENTS y aprueba (W04-6)."""
        return _accion(lambda: app.state.servicio.aprobar(siniestro_id, body.actor_usuario_id))

    @app.post("/siniestros/{siniestro_id}/rechazar", tags=["resolucion"])
    def rechazar(siniestro_id: str, body: RechazarRequest) -> dict:
        return _accion(
            lambda: app.state.servicio.rechazar(
                siniestro_id, body.motivo, body.comentario, body.actor_usuario_id
            )
        )

    @app.post("/siniestros/{siniestro_id}/derivar", tags=["resolucion"])
    def derivar(siniestro_id: str, body: DerivarRequest) -> dict:
        return _accion(
            lambda: app.state.servicio.derivar(
                siniestro_id, body.perito_id, body.instrucciones, body.actor_usuario_id
            )
        )

    if config.demo_reiniciar:

        @app.post("/_demo/reiniciar", tags=["operacion"])
        def reiniciar_demo() -> dict[str, int]:
            """Solo local (W04-15, `DEMO_REINICIAR=true`): deja los casos como en la semilla para
            las pruebas E2E de la web. Nunca existe en staging."""
            return {"siniestros": app.state.reiniciar()}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
