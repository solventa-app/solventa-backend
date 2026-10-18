"""Stub de la pasarela de pagos tokenizada.

Mismos 4 modos de falla que los stubs de Open Finance/Open Data (`sano`,
`lento`, `error`, `caido`) más `rechaza` (la pasarela responde, pero rechaza el
cobro), fijados en caliente vía `/control/modo` o por llamada vía query param
`modo`/header `X-Modo-Simulado`.

Como una pasarela real, **recuerda cada cobro por su referencia del comercio**
(`referencia_comercio`): repetir un cobro con la misma referencia devuelve el
resultado original sin cobrar de nuevo, y
`GET /v1/cobros/{referencia_comercio}` permite consultarlo (conciliación,
KAN-28). La memoria vive en el proceso: se reinicia limpia con el contenedor.

Modo `lento` realista: el cobro se registra **antes** de la espera, así que si
el ACL Worker corta a los 700 ms, el cobro igual quedó hecho en la pasarela
(es el caso que la conciliación debe detectar para no cobrar dos veces). Nunca recibe ni
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

from app.observabilidad import configurar_trazas

MODOS = ("sano", "lento", "error", "caido", "rechaza")
Modo = Literal["sano", "lento", "error", "caido", "rechaza"]
MOTIVO_RECHAZO = "fondos_insuficientes"

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


ESTADOS_ALTERNA = {"cobrado": "APPROVED", "rechazado": "DECLINED", "pendiente": "PENDING"}


class SolicitudCobroAlterna(BaseModel):
    """Segundo proveedor simulado (EC031): otra API, otros nombres de campos y otros estados."""

    reference: str
    policy: str
    amount: str
    currency: str
    payment_token: str


class SolicitudCobro(BaseModel):
    # "Comercio" es el punto de vista de la pasarela: el comercio somos nosotros.
    # Solventa la llama `id_intento_cobro` en su propio dominio, y el adaptador del
    # ACL Worker (`AdaptadorPasarela`) traduce entre los dos nombres.
    referencia_comercio: str
    poliza_id: str
    monto: str
    moneda: str
    token_medio_pago: str


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-pasarela", version="0.1.0")
    estado = {"modo": "sano"}
    # Cobros registrados por `referencia_comercio`.
    cobros: dict[str, dict] = {}
    candado = asyncio.Lock()

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _fallar_si_corresponde(modo: str) -> None:
        """Fallas que ocurren ANTES de procesar nada: no registran el cobro."""
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="pasarela_no_disponible")

    async def _esperar_respuesta(modo: str) -> None:
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    async def _aplicar_modo(modo: str) -> None:
        await _fallar_si_corresponde(modo)
        await _esperar_respuesta(modo)

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

    async def _registrar_cobro(referencia: str, modo: str) -> dict:
        """Registra el cobro por su referencia (una vez) y espera la respuesta simulada. Lo
        comparten las dos APIs del stub: repetir la referencia devuelve el resultado original."""
        await _fallar_si_corresponde(modo)
        async with candado:
            resultado = cobros.get(referencia)
            if resultado is None:
                # Cobro nuevo. Se registra ANTES de la espera de `lento`: si el
                # cliente corta, el cobro igual quedó hecho (como en la vida real).
                if modo == "rechaza":
                    resultado = {
                        "estado": "rechazado",
                        "referencia": None,
                        "motivo": MOTIVO_RECHAZO,
                    }
                else:
                    resultado = {
                        "estado": "cobrado",
                        "referencia": f"pg-{uuid.uuid4()}",
                        "motivo": None,
                    }
                resultado["detalle"] = {"referencia_comercio": referencia}
                cobros[referencia] = resultado
        await _esperar_respuesta(modo)
        return resultado

    @app.post("/v1/cobros", tags=["proveedor"])
    async def cobrar(
        body: SolicitudCobro,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        return await _registrar_cobro(body.referencia_comercio, modo_efectivo)

    @app.get("/v1/cobros/{referencia_comercio}", tags=["proveedor"])
    async def consultar_cobro(
        referencia_comercio: str,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        """Conciliación: ¿la pasarela tiene un cobro con esta referencia del comercio
        (`referencia_comercio`)? 404 si no. Obedece el modo simulado."""
        await _aplicar_modo(_modo_efectivo(modo, x_modo_simulado))
        resultado = cobros.get(referencia_comercio)
        if resultado is None:
            raise HTTPException(status_code=404, detail="cobro no encontrado")
        return resultado

    # --- Segundo proveedor (EC031): misma memoria de cobros y mismos modos, otra API ---

    def _a_alterna(referencia: str, resultado: dict) -> dict:
        return {
            "status": ESTADOS_ALTERNA[resultado["estado"]],
            "transaction_id": resultado["referencia"],
            "decline_code": resultado["motivo"],
            "reference": referencia,
        }

    @app.post("/alterna/v2/charges", tags=["proveedor-alterno"])
    async def cobrar_alterna(
        body: SolicitudCobroAlterna,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        resultado = await _registrar_cobro(body.reference, modo_efectivo)
        return _a_alterna(body.reference, resultado)

    @app.get("/alterna/v2/charges/{reference}", tags=["proveedor-alterno"])
    async def consultar_cobro_alterna(
        reference: str,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        await _aplicar_modo(_modo_efectivo(modo, x_modo_simulado))
        resultado = cobros.get(reference)
        if resultado is None:
            raise HTTPException(status_code=404, detail="charge_not_found")
        return _a_alterna(reference, resultado)

    @app.get("/alterna/v2/status", tags=["proveedor-alterno"])
    async def estado_alterna(
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        await _aplicar_modo(_modo_efectivo(modo, x_modo_simulado))
        return {"ok": True}

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


configurar_trazas("stub-pasarela")

app = create_app()
