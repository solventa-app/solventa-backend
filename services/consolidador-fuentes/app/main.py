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

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.observabilidad import configurar_trazas
from app.tareas import reconciliar_fuente
from app.worker import TrabajadorReconciliacion

# Mismo motivo que en acl-worker: sin esto, los logs INFO de app.worker/app.tareas
# (único rastro observable de que el trabajador procesó o reintentó un job) se
# descartan en silencio bajo uvicorn.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("consolidador-fuentes", instrumentos=("httpx", "redis"))


class TrabajoRequest(BaseModel):
    """Mismo contenido que el job de `rq` (`TrabajoReconciliacion` del ACL Worker)."""

    fuente: str
    cliente_id: str
    consentimiento_id: str
    encolado_en: float


def create_app(redis_url: str | None = None, disparador: str | None = None) -> FastAPI:
    """`disparador`: "bucle" (local y CI: trabajador de `rq` en un hilo de fondo) o "externo" (Cloud
    Run: sin trabajador; Cloud Tasks llama a `POST /reconciliar`). Por defecto, `DISPARADOR`."""
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/2")
    disparador = disparador or os.getenv("DISPARADOR", "bucle")
    if disparador not in ("bucle", "externo"):
        raise ValueError("DISPARADOR debe ser 'bucle' o 'externo'")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        trabajador = TrabajadorReconciliacion(redis_url) if disparador == "bucle" else None
        if trabajador is not None:
            trabajador.iniciar()
        app.state.trabajador = trabajador
        try:
            yield
        finally:
            if trabajador is not None:
                trabajador.detener()

    app = FastAPI(title="solventa-consolidador-fuentes", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "consolidador-fuentes", "estado": "ok"}

    @app.post("/reconciliar", tags=["reconciliacion"])
    def reconciliar(trabajo: TrabajoRequest) -> dict[str, str]:
        """La llama Cloud Tasks (`DISPARADOR=externo`) con el trabajo que antes viajaba por `rq`.
        200 = resuelto (o descartado por consentimiento no vigente: no se reintenta).
        503 = la fuente sigue degradada o AUTH/ACL no respondieron: Cloud Tasks reintenta con el
        backoff de su cola. Privada (IAM de Cloud Run)."""
        try:
            reconciliar_fuente(
                trabajo.fuente, trabajo.cliente_id, trabajo.consentimiento_id, trabajo.encolado_en
            )
        except Exception as error:  # cualquier fallo se reintenta; el detalle no se expone
            logging.getLogger("consolidador_fuentes.tareas").info(
                "reconciliacion fuente=%s pendiente de reintento (%s)",
                trabajo.fuente,
                type(error).__name__,
            )
            raise HTTPException(status_code=503, detail="reintentar") from error
        return {"estado": "resuelta"}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
