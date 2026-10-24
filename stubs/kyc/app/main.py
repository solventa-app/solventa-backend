"""Stub del proveedor de KYC (verificación de identidad), HU-W03, decisión W03-10.

Mismos modos de falla que los demás stubs (`sano`, `lento`, `error`, `caido`) más `rechaza` (el
proveedor responde, pero no puede verificar la identidad), fijados en caliente vía `/control/modo` o
por llamada vía query param `modo`/header `X-Modo-Simulado`.

Como un proveedor real, **recuerda cada verificación por su referencia** (`referencia_comercio`):
repetirla devuelve el resultado original. El modo `lento` registra la verificación **antes** de la
espera, así que si el ACL Worker corta a los 700 ms, la verificación igual quedó hecha.

**El contrato es inventado**, no el de un proveedor real (Truora u otro): un KYC real pide el
documento del cliente y una validación (p. ej. biométrica), y eso no pasa por este stub. Aquí solo
llega el identificador opaco del cliente (regla de arquitectura #6) y el resultado es determinista:
`aprobada`, salvo en modo `rechaza`. Ver `docs/plan-completar-hu-w03.md`, decisión W03-10.

Expone además `GET /v1/estado`, que **sí obedece el modo simulado** (a diferencia de `/health`): lo
usa la sonda de recuperación del circuito del ACL Worker, que no debe crear una verificación de
verdad solo para probar si el proveedor ya respondió.
"""

import asyncio
import random
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.observabilidad import configurar_trazas

MODOS = ("sano", "lento", "error", "caido", "rechaza")
Modo = Literal["sano", "lento", "error", "caido", "rechaza"]
MOTIVO_RECHAZO = "identidad_no_verificada"

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudVerificacion(BaseModel):
    # "Comercio" es el punto de vista del proveedor: el comercio somos nosotros. Solventa la llama
    # `id_verificacion` en su dominio y el adaptador del ACL Worker traduce entre los dos nombres.
    referencia_comercio: str
    cliente_id: str


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-kyc", version="0.1.0")
    estado = {"modo": "sano"}
    # Verificaciones registradas por referencia del comercio.
    verificaciones: dict[str, dict] = {}
    candado = asyncio.Lock()

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _fallar_si_corresponde(modo: str) -> None:
        """Fallas que ocurren ANTES de procesar nada: no registran la verificación."""
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="kyc_no_disponible")

    async def _esperar_respuesta(modo: str) -> None:
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-kyc", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.post("/v1/verificaciones", tags=["proveedor"])
    async def verificar(
        body: SolicitudVerificacion,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        async with candado:
            resultado = verificaciones.get(body.referencia_comercio)
            if resultado is None:
                # Verificación nueva. Se registra ANTES de la espera de `lento`: si el cliente
                # corta, igual quedó hecha (como en la vida real).
                rechazada = modo_efectivo == "rechaza"
                resultado = {
                    "estado": "rechazada" if rechazada else "aprobada",
                    "referencia": None if rechazada else f"kyc-{uuid.uuid4()}",
                    "motivo": MOTIVO_RECHAZO if rechazada else None,
                    "verificada_en": datetime.now(UTC).isoformat(),
                }
                verificaciones[body.referencia_comercio] = resultado
        await _esperar_respuesta(modo_efectivo)
        return resultado

    @app.get("/v1/verificaciones/{referencia_comercio}", tags=["proveedor"])
    async def consultar_verificacion(referencia_comercio: str) -> dict:
        """Para verificar en vivo qué quedó registrado: 404 si el proveedor no la tiene."""
        resultado = verificaciones.get(referencia_comercio)
        if resultado is None:
            raise HTTPException(status_code=404, detail="verificación no encontrada")
        return resultado

    @app.get("/v1/estado", tags=["proveedor"])
    async def estado_proveedor(
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        """Usado solo por la sonda de recuperación del circuito (ver docstring del módulo)."""
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        await _esperar_respuesta(modo_efectivo)
        return {"ok": True}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-kyc")

app = create_app()
