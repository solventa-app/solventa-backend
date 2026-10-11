"""AUTH: consentimiento e identidad.

Los endpoints de consentimiento responden hoy sobre un almacén **en memoria**
(ver `app/consentimientos.py`): es un mock hasta T-W01-4. El contrato HTTP es
el definitivo; solo cambiará el almacén."""

import logging

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.consentimientos import AlmacenConsentimientos, AlmacenConsentimientosMemoria
from app.identificadores import id_opaco

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("auth.consentimientos")


class SolicitudConsentimiento(BaseModel):
    finalidad: str
    version_texto: str


class ConsentimientoResponse(BaseModel):
    id: str
    finalidad: str
    version_texto: str
    otorgado_en: str
    revocado_en: str | None
    vigente: bool


def create_app(almacen: AlmacenConsentimientos | None = None) -> FastAPI:
    """`almacen` permite inyectar un doble de prueba; por defecto, el mock en memoria."""
    almacen = almacen or AlmacenConsentimientosMemoria()
    app = FastAPI(title="solventa-auth", version="0.1.0")

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "auth", "estado": "ok"}

    @app.post("/consentimientos", tags=["consentimientos"], response_model=ConsentimientoResponse)
    def registrar(body: SolicitudConsentimiento) -> ConsentimientoResponse:
        consentimiento = almacen.registrar(body.finalidad, body.version_texto)
        logger.info("consentimiento registrado id=%s", id_opaco(consentimiento.id))
        return ConsentimientoResponse(**consentimiento.como_dict())

    @app.get(
        "/consentimientos/{consentimiento_id}",
        tags=["consentimientos"],
        response_model=ConsentimientoResponse,
    )
    def obtener(consentimiento_id: str) -> ConsentimientoResponse:
        consentimiento = almacen.obtener(consentimiento_id)
        if consentimiento is None:
            raise HTTPException(status_code=404, detail="consentimiento no encontrado")
        return ConsentimientoResponse(**consentimiento.como_dict())

    @app.post(
        "/consentimientos/{consentimiento_id}/revocar",
        tags=["consentimientos"],
        response_model=ConsentimientoResponse,
    )
    def revocar(consentimiento_id: str) -> ConsentimientoResponse:
        consentimiento = almacen.revocar(consentimiento_id)
        if consentimiento is None:
            raise HTTPException(status_code=404, detail="consentimiento no encontrado")
        logger.info("consentimiento revocado id=%s", id_opaco(consentimiento.id))
        return ConsentimientoResponse(**consentimiento.como_dict())

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
