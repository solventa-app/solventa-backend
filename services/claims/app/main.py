"""CLAIMS: escritor único de la mesa de siniestros (HU-W04).

Servicio privado: solo lo llama el BFF (IAM de Cloud Run). La autorización por rol (solo BACKOFFICE)
la hace el BFF antes de llamar (W04-11).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pymongo import MongoClient

from app.application.cifrado import CifradorAesGcm
from app.application.configuracion import Configuracion
from app.application.repositorio import RepositorioSiniestrosMongo
from app.application.servicio_siniestros import ServicioSiniestros
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("claims", instrumentos=("httpx", "pymongo"))


class EstadoSiniestro(StrEnum):
    PENDIENTE = "PENDIENTE"
    EN_REVISION = "EN_REVISION"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


def create_app(
    config: Configuracion | None = None,
    servicio: ServicioSiniestros | None = None,
) -> FastAPI:
    """`servicio` permite inyectar dobles de prueba sin levantar MongoDB (ver `tests/fakes.py`)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_mongo: MongoClient | None = None
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
            app.state.servicio = ServicioSiniestros(repositorio)
        try:
            yield
        finally:
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

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
