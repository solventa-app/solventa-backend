"""UNDER: decisión de suscripción (HU-W03, KAN-213).

Sin almacén: decide con el KYC y unas reglas simples, y responde. Habla con el proveedor de KYC SOLO
a través del ACL Worker (regla de arquitectura #2). Lo llama POLICY al emitir (bloque 4).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field, field_validator

from app.application.autenticacion_servicios import autenticacion_para
from app.application.cliente_acl import ClienteAcl
from app.application.configuracion import Configuracion
from app.application.servicio_decisiones import ServicioDecisiones
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("under", instrumentos=("httpx",))


class MontoRequest(BaseModel):
    valor: str  # decimal como texto, nunca float
    moneda: str = Field(min_length=1)

    @field_validator("valor")
    @classmethod
    def _decimal_valido(cls, valor: str) -> str:
        try:
            if Decimal(valor) < 0:
                raise ValueError("el valor no puede ser negativo")
        except InvalidOperation as error:
            raise ValueError("el valor debe ser un decimal") from error
        return valor


class DecisionRequest(BaseModel):
    propuesta_id: str = Field(min_length=1)
    # El identificador opaco (HMAC) del cliente, nunca un documento.
    cliente_id: str = Field(min_length=1)
    # `BAJO`, `MEDIO` o `ALTO` (el `perfilRiesgo` de la oferta). Sin él no se aprueba sola.
    nivel_riesgo: str | None = None
    cobertura: MontoRequest


class KycResponse(BaseModel):
    estado: str
    referencia: str | None = None


class DecisionResponse(BaseModel):
    decision: str  # APROBADA | RECHAZADA | REVISION_MANUAL
    motivos: list[str]
    kyc: KycResponse


def create_app(
    config: Configuracion | None = None, servicio: ServicioDecisiones | None = None
) -> FastAPI:
    """`servicio` permite inyectar un doble de prueba sin el ACL Worker real (ver `tests/fakes.py`).
    En producción se omite y `create_app` lo construye desde `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_acl: ClienteAcl | None = None
        if servicio is not None:
            app.state.servicio = servicio
        else:
            cliente_acl = ClienteAcl(
                config.acl_url,
                config.acl_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "acl-worker"),
            )
            app.state.servicio = ServicioDecisiones(cliente_acl, config.cobertura_maxima_automatica)
        try:
            yield
        finally:
            if cliente_acl is not None:
                cliente_acl.cerrar()

    app = FastAPI(title="solventa-under", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "under", "estado": "ok"}

    @app.post("/decisiones", tags=["decisiones"], response_model=DecisionResponse)
    def decidir(body: DecisionRequest) -> DecisionResponse:
        """CA-W03-07 y CA-W03-09: decide la suscripción. Siempre 200 con una decisión: si el KYC no
        responde, la propuesta va a `REVISION_MANUAL` (regla #4), nunca un 5xx por el proveedor."""
        resultado = app.state.servicio.decidir(
            body.propuesta_id,
            body.cliente_id,
            body.nivel_riesgo,
            Decimal(body.cobertura.valor),
            body.cobertura.moneda,
        )
        return DecisionResponse(
            decision=resultado.decision.decision,
            motivos=resultado.decision.motivos,
            kyc=KycResponse(estado=resultado.kyc.estado, referencia=resultado.kyc.referencia),
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
