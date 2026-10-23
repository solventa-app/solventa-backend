"""Stub del proveedor de firma electrónica (HU-W03, CA-W03-03).

Mismos modos de falla que los demás stubs (`sano`, `lento`, `error`, `caido`), fijados en caliente
vía `/control/modo` o por llamada vía query param `modo`/header `X-Modo-Simulado`.

Como un proveedor de firma real, **recuerda cada firma por su referencia** (`referencia_comercio`):
repetir una firma con la misma referencia devuelve la evidencia original sin firmar de nuevo. Es lo
que hace seguro reintentar tras un corte por tiempo: modo `lento` registra la firma **antes** de la
espera, así que si el ACL Worker corta a los 700 ms, la firma igual quedó hecha. La memoria vive en
el proceso: se reinicia limpia con el contenedor.

Solo recibe el **hash** del documento, su versión y el identificador opaco del firmante: nunca el
contenido del documento ni datos personales (regla de arquitectura #6).

Expone además `GET /v1/estado`, que **sí obedece el modo simulado** (a diferencia de `/health`):
es lo que usa la sonda de recuperación del circuito del ACL Worker, que no debe crear una firma de
verdad solo para probar si el proveedor ya respondió.

Incluye un segundo proveedor simulado (`/alterna/v1/...`) con otra API, para demostrar EC031 /
CA-W03-10: cambiar de proveedor de firma es agregar un adaptador.
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


class SolicitudFirma(BaseModel):
    # "Comercio" es el punto de vista del proveedor: el comercio somos nosotros. Solventa la llama
    # `id_firma` en su dominio y el adaptador del ACL Worker traduce entre los dos nombres.
    referencia_comercio: str
    documento_hash: str
    version_documento: str
    firmante_id: str


class SolicitudFirmaAlterna(BaseModel):
    """Segundo proveedor simulado (EC031): otra API y otros nombres de campos."""

    signature_request_id: str
    document_digest: str
    document_version: str
    signer: str


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-firma", version="0.1.0")
    estado = {"modo": "sano"}
    # Firmas registradas por referencia del comercio.
    firmas: dict[str, dict] = {}
    candado = asyncio.Lock()

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _fallar_si_corresponde(modo: str) -> None:
        """Fallas que ocurren ANTES de procesar nada: no registran la firma."""
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="firma_no_disponible")

    async def _esperar_respuesta(modo: str) -> None:
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-firma", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    async def _registrar_firma(referencia: str, documento_hash: str, modo: str) -> dict:
        """Registra la firma por su referencia (una vez) y espera la respuesta simulada. La
        comparten las dos APIs del stub: repetir la referencia devuelve la evidencia original."""
        await _fallar_si_corresponde(modo)
        async with candado:
            firma = firmas.get(referencia)
            if firma is None:
                # Firma nueva. Se registra ANTES de la espera de `lento`: si el cliente corta, la
                # firma igual quedó hecha (como en la vida real).
                firma = {
                    "estado": "firmada",
                    "referencia": f"fe-{uuid.uuid4()}",
                    "firmada_en": datetime.now(UTC).isoformat(),
                    "motivo": None,
                    "documento_hash": documento_hash,
                }
                firmas[referencia] = firma
        await _esperar_respuesta(modo)
        return firma

    @app.post("/v1/firmas", tags=["proveedor"])
    async def firmar(
        body: SolicitudFirma,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        firma = await _registrar_firma(body.referencia_comercio, body.documento_hash, modo_efectivo)
        return {k: v for k, v in firma.items() if k != "documento_hash"}

    @app.get("/v1/firmas/{referencia_comercio}", tags=["proveedor"])
    async def consultar_firma(referencia_comercio: str) -> dict:
        """Para verificar en vivo qué quedó registrado: 404 si el proveedor no la tiene."""
        firma = firmas.get(referencia_comercio)
        if firma is None:
            raise HTTPException(status_code=404, detail="firma no encontrada")
        return firma

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

    # --- Segundo proveedor (EC031): misma memoria de firmas y mismos modos, otra API ---

    @app.post("/alterna/v1/signatures", tags=["proveedor-alterno"])
    async def firmar_alterna(
        body: SolicitudFirmaAlterna,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        firma = await _registrar_firma(
            body.signature_request_id, body.document_digest, modo_efectivo
        )
        return {
            "status": "SIGNED",
            "signature_id": firma["referencia"],
            "signed_at": firma["firmada_en"],
        }

    @app.get("/alterna/v1/status", tags=["proveedor-alterno"])
    async def estado_alterna(
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        await _esperar_respuesta(modo_efectivo)
        return {"ok": True}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-firma")

app = create_app()
