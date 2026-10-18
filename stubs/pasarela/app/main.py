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

**Tokenización (KAN-208).** `POST /v1/tokens` hace lo que una pasarela real con su formulario
de captura: recibe la tarjeta (solo tarjetas de prueba, validadas con Luhn) y devuelve un token
opaco con la marca y los últimos 4 dígitos. El número y el CVV **no se guardan, no se devuelven
ni se escriben en ningún log**; los errores de validación tampoco los repiten. El token se deriva
de la tarjeta para que tokenizar la misma tarjeta de prueba dé siempre el mismo token (la
siembra de la demo es repetible, aunque el contenedor se reinicie). Eso es solo del simulador.
Un token que el simulador no conoce se sigue aceptando al cobrar (compatibilidad con las
pruebas y los cobros anteriores).

Controles de la demo, siempre bajo `/control`:
- `POST /control/tokens/{token}/estado`: cambia a mano el estado de una tarjeta
  (`activa`, `fondos_insuficientes`, `bloqueada`, `vencida`). Un cobro con una tarjeta que no
  está `activa` se **rechaza** con su motivo, sin importar el modo global.
- `POST /control/latencia`: fija cuánto tarda en responder la pasarela (milisegundos). Por
  encima de los 700 ms del ACL Worker equivale a una falla por timeout. `DELETE` la restaura.

Expone además `GET /v1/estado`, que **sí obedece el modo simulado** (a
diferencia de `/health`, que siempre responde ok como liveness del
contenedor). `/v1/estado` es lo que usa la sonda de recuperación del circuito
del ACL Worker: sondear con un cobro real tendría un efecto de negocio (crear
una orden de pago) que no queremos disparar solo para probar si el proveedor
ya respondió.
"""

import asyncio
import hashlib
import random
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

MODOS = ("sano", "lento", "error", "caido", "rechaza")
Modo = Literal["sano", "lento", "error", "caido", "rechaza"]
MOTIVO_RECHAZO = "fondos_insuficientes"

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


EstadoTarjeta = Literal["activa", "fondos_insuficientes", "bloqueada", "vencida"]
# Motivo con el que la pasarela rechaza un cobro según el estado de la tarjeta.
MOTIVO_POR_ESTADO = {
    "fondos_insuficientes": "fondos_insuficientes",
    "bloqueada": "tarjeta_bloqueada",
    "vencida": "tarjeta_vencida",
}
LATENCIA_MAXIMA_MS = 120_000


class SolicitudTarjeta(BaseModel):
    """Solo tarjetas de prueba. Estos campos no se guardan ni se devuelven jamás."""

    numero: str
    vencimiento_mes: int
    vencimiento_anio: int
    cvv: str
    titular: str | None = None


class SolicitudEstadoTarjeta(BaseModel):
    estado: EstadoTarjeta


class SolicitudLatencia(BaseModel):
    minimo_ms: int
    maximo_ms: int


def _luhn_valido(numero: str) -> bool:
    suma = 0
    for posicion, digito in enumerate(reversed(numero)):
        valor = int(digito)
        if posicion % 2 == 1:
            valor *= 2
            if valor > 9:
                valor -= 9
        suma += valor
    return suma % 10 == 0


def _marca(numero: str) -> str:
    if numero.startswith("4"):
        return "visa"
    if numero[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(numero[:4]) <= 2720:
        return "mastercard"
    if numero[:2] in {"34", "37"}:
        return "amex"
    return "otra"


def _token_de(numero: str, mes: int, anio: int) -> str:
    """Token determinista (solo del simulador): la misma tarjeta de prueba da el mismo token."""
    return "tok_" + hashlib.sha256(f"{numero}:{mes}:{anio}".encode()).hexdigest()[:24]


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
    # Tarjetas tokenizadas: token -> marca, últimos 4, vencimiento y estado. Nunca el número.
    tokens: dict[str, dict] = {}
    # Si está fijada, reemplaza la espera de todos los modos (excepto `caido`).
    latencia: dict[str, int | None] = {"minimo_ms": None, "maximo_ms": None}
    candado = asyncio.Lock()

    @app.exception_handler(RequestValidationError)
    async def solicitud_invalida(_: Request, error: RequestValidationError) -> JSONResponse:
        """422 sin repetir lo recibido: FastAPI incluiría `input`, y ahí viajaría la tarjeta."""
        detalle = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": detalle})

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
        if latencia["minimo_ms"] is not None:
            await asyncio.sleep(random.uniform(latencia["minimo_ms"], latencia["maximo_ms"]) / 1000)
        elif modo == "lento":
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

    @app.get("/control/latencia", tags=["control"])
    def obtener_latencia() -> dict:
        return {"minimo_ms": latencia["minimo_ms"], "maximo_ms": latencia["maximo_ms"]}

    @app.post("/control/latencia", tags=["control"])
    def fijar_latencia(body: SolicitudLatencia) -> dict:
        """Cuánto tarda en responder la pasarela, en milisegundos (entre `minimo_ms` y `maximo_ms`).
        Por encima de los 700 ms del ACL Worker equivale a una falla por timeout."""
        if not 0 <= body.minimo_ms <= body.maximo_ms <= LATENCIA_MAXIMA_MS:
            raise HTTPException(
                status_code=422,
                detail=f"se requiere 0 <= minimo_ms <= maximo_ms <= {LATENCIA_MAXIMA_MS}",
            )
        latencia.update(minimo_ms=body.minimo_ms, maximo_ms=body.maximo_ms)
        return {"minimo_ms": latencia["minimo_ms"], "maximo_ms": latencia["maximo_ms"]}

    @app.delete("/control/latencia", tags=["control"])
    def restaurar_latencia() -> dict:
        """Vuelve a la latencia propia de cada modo."""
        latencia.update(minimo_ms=None, maximo_ms=None)
        return {"minimo_ms": None, "maximo_ms": None}

    @app.get("/control/tokens", tags=["control"])
    def listar_tokens() -> list[dict]:
        return [{"token": token, **datos} for token, datos in tokens.items()]

    @app.post("/control/tokens/{token}/estado", tags=["control"])
    def fijar_estado_tarjeta(token: str, body: SolicitudEstadoTarjeta) -> dict:
        """Cambia a mano el estado de una tarjeta: un cobro con una tarjeta que no está `activa`
        se rechaza con su motivo, sin importar el modo global."""
        if token not in tokens:
            raise HTTPException(status_code=404, detail="token no encontrado")
        tokens[token]["estado"] = body.estado
        return {"token": token, **tokens[token]}

    @app.post("/v1/tokens", tags=["proveedor"])
    async def tokenizar(
        body: SolicitudTarjeta,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        """Tokeniza una tarjeta de PRUEBA. Idempotente: la misma tarjeta da el mismo token y
        conserva su estado. El número y el CVV no se guardan ni se devuelven."""
        await _aplicar_modo(_modo_efectivo(modo, x_modo_simulado))
        numero = "".join(c for c in body.numero if c not in " -")
        if not numero.isdigit() or not 13 <= len(numero) <= 19 or not _luhn_valido(numero):
            raise HTTPException(status_code=422, detail="numero_invalido")
        if not body.cvv.isdigit() or len(body.cvv) not in (3, 4):
            raise HTTPException(status_code=422, detail="cvv_invalido")
        if not 1 <= body.vencimiento_mes <= 12:
            raise HTTPException(status_code=422, detail="vencimiento_invalido")
        hoy = datetime.now(UTC)
        if (body.vencimiento_anio, body.vencimiento_mes) < (hoy.year, hoy.month):
            raise HTTPException(status_code=422, detail="tarjeta_vencida")
        token = _token_de(numero, body.vencimiento_mes, body.vencimiento_anio)
        tokens.setdefault(
            token,
            {
                "marca": _marca(numero),
                "ultimos4": numero[-4:],
                "vencimiento_mes": body.vencimiento_mes,
                "vencimiento_anio": body.vencimiento_anio,
                "estado": "activa",
            },
        )
        return {"token": token, **tokens[token]}

    @app.get("/v1/tokens/{token}", tags=["proveedor"])
    async def consultar_token(token: str) -> dict:
        if token not in tokens:
            raise HTTPException(status_code=404, detail="token no encontrado")
        return {"token": token, **tokens[token]}

    @app.post("/v1/cobros", tags=["proveedor"])
    async def cobrar(
        body: SolicitudCobro,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        async with candado:
            resultado = cobros.get(body.referencia_comercio)
            if resultado is None:
                # Cobro nuevo. Se registra ANTES de la espera de `lento`: si el
                # cliente corta, el cobro igual quedó hecho (como en la vida real).
                tarjeta = tokens.get(body.token_medio_pago)
                if tarjeta is not None and tarjeta["estado"] != "activa":
                    resultado = {
                        "estado": "rechazado",
                        "referencia": None,
                        "motivo": MOTIVO_POR_ESTADO[tarjeta["estado"]],
                    }
                elif modo_efectivo == "rechaza":
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
                resultado["detalle"] = {"referencia_comercio": body.referencia_comercio}
                cobros[body.referencia_comercio] = resultado
        await _esperar_respuesta(modo_efectivo)
        return resultado

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
