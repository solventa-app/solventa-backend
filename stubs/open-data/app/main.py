"""Stub de Open Data (3 fuentes abiertas).

Mismo contrato y mismos modos de falla que `stub-open-finance` (ver ese README):
imita un proveedor externo síncrono con un endpoint de control para forzar
`sano`, `lento`, `error` o `caido` en caliente, para poder probar el Circuit
Breaker del ACL Worker de verdad. Andamiaje de prueba: sin hexagonal, sin
persistencia.
"""

import asyncio
import random
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

FUENTES = ("afiliacion-pila", "camara-comercio", "antecedentes-judiciales")
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
    if fuente == "afiliacion-pila":
        return {"estado_afiliacion": "activo", "antiguedad_meses": 36}
    if fuente == "camara-comercio":
        return {"existe_registro": True, "actividad_economica": "otros servicios"}
    return {"antecedentes_vigentes": False}


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-open-data", version="0.1.0")
    estado = {"modo": "sano"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-open-data", "estado": "ok"}

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
