"""Stub de Open Finance (2 fuentes financieras, circular 004 de 2024 de la SFC).

Imita el contrato mínimo de un proveedor real: consulta síncrona por fuente y un
endpoint de control para forzar modos de falla en caliente (T-W05-2 / T-W01-8),
en el estilo de `stub-kyc` del Experimento 1 (`solventa-arquitectura@11e4be6`),
sin ciclo asíncrono crear→pollear (eso era específico de Truora/KYC, fuera de
alcance del Sprint 1) y sin estructura hexagonal (es andamiaje de prueba).
"""

import asyncio
import random
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

FUENTES = ("cuentas-bancarias", "historial-crediticio")
MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudConsulta(BaseModel):
    cliente_id: str


def _datos_simulados(fuente: str) -> dict:
    if fuente == "cuentas-bancarias":
        return {"saldo_promedio_3m": 4_250_000, "productos_activos": 2}
    return {"score_crediticio": 712, "obligaciones_vigentes": 1}


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-open-finance", version="0.1.0")
    estado = {"modo": "sano"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        """El query param o el header, si vienen, ganan por esa sola llamada (no persisten).
        Sin ninguno de los dos, se usa el modo global fijado por /control/modo."""
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        # El health check siempre responde ok, sin importar el modo simulado: es la
        # sonda de vida del contenedor, no la sonda de recuperación del circuito.
        return {"servicio": "stub-open-finance", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.post("/v1/fuentes/{fuente}/consulta", tags=["proveedor"])
    async def consultar(
        fuente: str,
        body: SolicitudConsulta,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        if fuente not in FUENTES:
            raise HTTPException(status_code=404, detail=f"fuente desconocida: {fuente}")

        modo = _modo_efectivo(modo, x_modo_simulado)

        if modo == "caido":
            # Simula un proveedor totalmente caído: el socket queda colgado hasta que
            # el cliente (adaptador del ACL Worker) cancele por su propio timeout duro.
            await asyncio.sleep(60)

        if modo == "error":
            raise HTTPException(status_code=503, detail="proveedor_no_disponible")

        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

        return {
            "fuente": fuente,
            "datos": _datos_simulados(fuente),
            "capturado_en": datetime.now(UTC).isoformat(),
        }

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
