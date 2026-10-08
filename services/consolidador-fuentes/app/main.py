"""Consolidador de fuentes: reconciliación diferida de Open Finance/Open Data
degradadas.

Ampliación de alcance pedida directamente por el usuario (no estaba en
`docs/sprint-1.md` original) — ver la nota en ese archivo y en
`docs/arquitectura-backend.md`. Diseño TRADUCIDO (no copiado) del
Consolidador KYC del Experimento 1 (Node.js/TypeScript + BullMQ) a
Python + `rq`, aplicado a Open Finance y Open Data (KYC sigue diferido al
Sprint 2).

Consume la cola `fuentes-reconciliacion` (lado productor en
`services/acl-worker/app/application/reconciliacion.py`) y repite la consulta
real llamando al ACL Worker por HTTP (`app/tareas.py`) — nunca al proveedor
directo (regla de arquitectura #2): el ACL Worker sigue siendo el único punto
de contacto con proveedores externos.

Sin arquitectura hexagonal (regla de arquitectura #3): es andamiaje de
reconciliación, no el límite anticorrupción con proveedores externos — ese
límite ya lo tiene `acl-worker`.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.worker import TrabajadorReconciliacion

# Mismo motivo que en acl-worker: sin esto, los logs INFO de app.worker/app.tareas
# (único rastro observable de que el trabajador procesó o reintentó un job) se
# descartan en silencio bajo uvicorn.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(redis_url: str | None = None) -> FastAPI:
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/2")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        trabajador = TrabajadorReconciliacion(redis_url)
        trabajador.iniciar()
        app.state.trabajador = trabajador
        try:
            yield
        finally:
            trabajador.detener()

    app = FastAPI(title="solventa-consolidador-fuentes", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "consolidador-fuentes", "estado": "ok"}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
