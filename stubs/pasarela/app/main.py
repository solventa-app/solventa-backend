"""Stub de la pasarela de pagos tokenizada.

Mismos 4 modos de falla que los stubs de Open Finance/Open Data (`sano`,
`lento`, `error`, `caido`), fijados en caliente vía `/control/modo` o por
llamada vía query param `modo`/header `X-Modo-Simulado`. Nunca recibe ni
almacena datos de tarjeta: solo un `token_medio_pago` opaco (ya tokenizado
por el proveedor PCI-DSS), como exige CA-W05-03 y la regla de arquitectura #6.

Expone además `GET /v1/estado`, que **sí obedece el modo simulado** (a
diferencia de `/health`, que siempre responde ok como liveness del
contenedor). `/v1/estado` es lo que usa la sonda de recuperación del circuito
del ACL Worker: sondear con un cobro real tendría un efecto de negocio (crear
una orden de pago) que no queremos disparar solo para probar si el proveedor
ya respondió.
"""

import asyncio
import random
import uuid
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudCobro(BaseModel):
    clave_idempotencia: str
    poliza_id: str
    monto: str
    moneda: str
    token_medio_pago: str


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-pasarela", version="0.1.0")
    estado = {"modo": "sano"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _aplicar_modo(modo: str) -> None:
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="pasarela_no_disponible")
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-pasarela", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.post("/v1/cobros", tags=["proveedor"])
    async def cobrar(
        body: SolicitudCobro,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _aplicar_modo(modo_efectivo)
        return {
            "estado": "cobrado",
            "referencia": f"pg-{uuid.uuid4()}",
            "detalle": {"clave_idempotencia": body.clave_idempotencia},
        }

    @app.get("/v1/estado", tags=["proveedor"])
    async def estado_proveedor(
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        """Usado solo por la sonda de recuperación del circuito (ver docstring del módulo)."""
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _aplicar_modo(modo_efectivo)
        return {"ok": True}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
