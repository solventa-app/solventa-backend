"""Stub del proveedor de notificaciones (HU-W03, bloque 5, CA-W03-08).

Mismos modos de falla que los demás stubs (`sano`, `lento`, `error`, `caido`), fijados en caliente
vía `/control/modo` o por llamada vía query param `modo`/header `X-Modo-Simulado`.

Como un proveedor real, **recuerda cada aviso por su referencia** (`referencia_comercio`): repetirlo
devuelve el aviso original **sin enviar otro**. El modo `lento` registra el aviso **antes** de la
espera, así que si el ACL Worker corta a los 700 ms, el aviso igual quedó enviado.

No manda nada de verdad: guarda en memoria lo que "envió" y lo expone en `GET /control/enviados`,
que es lo que las pruebas usan para comprobar que se avisó **una sola vez**.

**El contrato es inventado**, no el de un proveedor real. Un proveedor real resolvería el contacto
(correo o teléfono) del `destinatario_id` con su propia base; aquí no hay contacto. Solo llegan la
plantilla, el identificador opaco del usuario y los datos de la plantilla (número de póliza y ruta
del documento): nunca el contenido del documento ni datos personales (regla de arquitectura #6).

`GET /v1/estado` **sí obedece el modo simulado** (a diferencia de `/health`): lo usa la sonda de
recuperación del circuito del ACL Worker, que no debe mandar un aviso de verdad.
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

MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudNotificacion(BaseModel):
    # "Comercio" es el punto de vista del proveedor: el comercio somos nosotros. Solventa la llama
    # `id_notificacion` en su dominio y el adaptador del ACL Worker traduce entre los dos nombres.
    referencia_comercio: str
    plantilla: str
    destinatario_id: str
    datos: dict[str, str] = {}


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-notificaciones", version="0.1.0")
    estado = {"modo": "sano"}
    # Avisos registrados por referencia del comercio.
    enviadas: dict[str, dict] = {}
    candado = asyncio.Lock()

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _fallar_si_corresponde(modo: str) -> None:
        """Fallas que ocurren ANTES de procesar nada: no registran el aviso."""
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="notificaciones_no_disponibles")

    async def _esperar_respuesta(modo: str) -> None:
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-notificaciones", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.get("/control/enviados", tags=["control"])
    def listar_enviados() -> list[dict]:
        """Lo que el stub "envió", para comprobar en las pruebas que se avisó una sola vez."""
        return list(enviadas.values())

    @app.post("/v1/notificaciones", tags=["proveedor"])
    async def enviar(
        body: SolicitudNotificacion,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        async with candado:
            aviso = enviadas.get(body.referencia_comercio)
            if aviso is None:
                # Aviso nuevo. Se registra ANTES de la espera de `lento`: si el cliente corta,
                # igual quedó enviado (como en la vida real).
                aviso = {
                    "referencia_comercio": body.referencia_comercio,
                    "plantilla": body.plantilla,
                    "destinatario_id": body.destinatario_id,
                    "datos": body.datos,
                    "referencia": f"nt-{uuid.uuid4()}",
                    "enviada_en": datetime.now(UTC).isoformat(),
                }
                enviadas[body.referencia_comercio] = aviso
        await _esperar_respuesta(modo_efectivo)
        return {"estado": "enviada", "referencia": aviso["referencia"], "motivo": None}

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


configurar_trazas("stub-notificaciones")

app = create_app()
