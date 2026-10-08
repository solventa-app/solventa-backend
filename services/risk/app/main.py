"""RISK: único escritor del perfil de riesgo."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from pymongo import MongoClient

from app.application.bus_eventos import BusEventosLog
from app.application.configuracion import Configuracion
from app.application.repositorio_perfiles import RepositorioPerfilesMongo
from app.application.servicio_perfiles import ServicioPerfiles

# Sin esto, los logs INFO de app.application.servicio_perfiles/bus_eventos se
# descartan en silencio (mismo motivo documentado en acl-worker/app/main.py).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class DatosFuenteEntrada(BaseModel):
    """Mismo shape que ya devuelve `acl-worker` en `POST /fuentes/consultar`
    (`DatosFuenteResponse`): no se inventa un contrato nuevo, se reusa."""

    fuente: str
    datos: dict
    capturado_en: str
    de_cache: bool = False
    degradado: bool = False


class CrearPerfilRequest(BaseModel):
    cliente_id: str
    fuentes: list[DatosFuenteEntrada]


class CrearPerfilResponse(BaseModel):
    cliente_id: str
    perfil_version: int
    operation_time: str


def create_app(
    config: Configuracion | None = None,
    servicio_perfiles: ServicioPerfiles | None = None,
) -> FastAPI:
    """`servicio_perfiles` permite inyectar un `ServicioPerfiles` ya
    construido (p. ej. con dobles de prueba) sin levantar MongoDB — así las
    pruebas unitarias no dependen de un replica set real (ver
    `tests/fakes.py`). En producción (o al verificar en vivo con
    `docker compose`) se omite y `create_app` construye todo desde
    `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_mongo: MongoClient | None = None
        if servicio_perfiles is not None:
            app.state.servicio_perfiles = servicio_perfiles
        else:
            cliente_mongo = MongoClient(config.mongo_uri)
            app.state.servicio_perfiles = ServicioPerfiles(
                RepositorioPerfilesMongo(cliente_mongo), BusEventosLog()
            )
        try:
            yield
        finally:
            if cliente_mongo is not None:
                cliente_mongo.close()

    app = FastAPI(title="solventa-risk", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "risk", "estado": "ok"}

    @app.post("/perfiles", tags=["perfiles"], response_model=CrearPerfilResponse)
    def crear_perfil(body: CrearPerfilRequest) -> CrearPerfilResponse:
        resultado = app.state.servicio_perfiles.registrar(
            body.cliente_id,
            [fuente.model_dump() for fuente in body.fuentes],
        )
        return CrearPerfilResponse(**resultado)

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
