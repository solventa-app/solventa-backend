"""RATING: cálculo de oferta sobre réplica de lectura."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from prometheus_fastapi_instrumentator import Instrumentator
from pymongo import MongoClient

from app.application.configuracion import Configuracion
from app.application.lector_perfiles import LectorPerfiles, LectorPerfilesMongo

# Timeouts acotados (mismo presupuesto que `max_time_ms`) SOLO en el cliente
# dedicado a la secundaria — hueco real encontrado al verificar en vivo con
# `docker pause` sobre `mongo2` (ver README.md y
# `app/application/lector_perfiles.py`), cerrado en dos capas:
# - `serverSelectionTimeoutMS`: cuánto tarda en decidir que NO hay secundaria
#   utilizable, si la topología ya sabe que está caída (por defecto 30s).
# - `socketTimeoutMS`/`connectTimeoutMS`: la topología puede seguir creyendo
#   que mongo2 es una secundaria sana (el `docker pause` no cierra el socket,
#   solo congela el proceso) y despachar la consulta ahí — sin esto, la
#   lectura se queda bloqueada indefinidamente esperando una respuesta que
#   nunca llega (el default de pymongo es SIN timeout de socket).

# Mismo motivo que en acl-worker/risk: sin esto los logs INFO/WARNING de
# app.application.lector_perfiles (único rastro observable del fallback a
# primaria, D-02) se descartan en silencio.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(
    config: Configuracion | None = None,
    lector_perfiles: LectorPerfiles | None = None,
) -> FastAPI:
    """`lector_perfiles` permite inyectar un doble de prueba (ver
    `tests/fakes.py`) sin levantar un replica set real. En producción (o al
    verificar en vivo con `docker compose`) se omite y `create_app` lo
    construye desde `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_primaria: MongoClient | None = None
        cliente_secundaria: MongoClient | None = None
        if lector_perfiles is not None:
            app.state.lector_perfiles = lector_perfiles
        else:
            cliente_primaria = MongoClient(config.mongo_uri)
            cliente_secundaria = MongoClient(
                config.mongo_uri,
                serverSelectionTimeoutMS=config.max_time_ms,
                connectTimeoutMS=config.max_time_ms,
                socketTimeoutMS=config.max_time_ms,
            )
            app.state.lector_perfiles = LectorPerfilesMongo(
                cliente_primaria, cliente_secundaria, config.max_time_ms
            )
        try:
            yield
        finally:
            if cliente_primaria is not None:
                cliente_primaria.close()
            if cliente_secundaria is not None:
                cliente_secundaria.close()

    app = FastAPI(title="solventa-rating", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "rating", "estado": "ok"}

    @app.get("/perfiles/{cliente_id}", tags=["perfiles"])
    def obtener_perfil(cliente_id: str, operation_time: str = Query(...)) -> dict:
        """`operation_time`: el valor que RISK devolvió en `POST /perfiles`
        (D-02). Hoy se pasa directo en la petición (en producción lo
        reenviaría el BFF)."""
        try:
            perfil = app.state.lector_perfiles.leer(cliente_id, operation_time)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail=f"operation_time invalido: {error}"
            ) from error
        if perfil is None:
            raise HTTPException(status_code=404, detail="perfil no encontrado")
        return perfil

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
