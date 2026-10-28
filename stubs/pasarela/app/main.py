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

**Página de pago alojada (W03-28, HU-W03):** como PayU o Stripe, la pasarela sirve su propia
página. Solventa abre una sesión (`POST /v1/sesiones-pago`, vence a los 10 min), el navegador va a
`GET /checkout/{id}`, el stub captura la tarjeta, la **tokeniza** y cobra, y «Regresar al comercio»
lleva a `url_retorno?sesion=<id>` (lista blanca `URLS_RETORNO_PERMITIDAS`). Solventa confirma con
`GET /v1/sesiones-pago/{id}`, que con `COBRADA` entrega el token, la marca y el tipo; nunca la
tarjeta. Ningún dato de tarjeta se guarda ni se escribe en un log (`app/tarjetas.py`).

Expone además `GET /v1/estado`, que **sí obedece el modo simulado** (a
diferencia de `/health`, que siempre responde ok como liveness del
contenedor). `/v1/estado` es lo que usa la sonda de recuperación del circuito
del ACL Worker: sondear con un cobro real tendría un efecto de negocio (crear
una orden de pago) que no queremos disparar solo para probar si el proveedor
ya respondió.
"""

import asyncio
import os
import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app import pagina, tarjetas
from app.observabilidad import configurar_trazas

MODOS = ("sano", "lento", "error", "caido", "rechaza")
Modo = Literal["sano", "lento", "error", "caido", "rechaza"]
MOTIVO_RECHAZO = "fondos_insuficientes"

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


# Estados de una sesión de pago. VENCIDA se fija al consultarla después de `vence_en`.
ESTADOS_SESION = ("ABIERTA", "PROCESANDO", "COBRADA", "RECHAZADA", "FALLIDA", "VENCIDA")
ESTADOS_SESION_ALTERNA = {
    "ABIERTA": "OPEN",
    "PROCESANDO": "PROCESSING",
    "COBRADA": "SUCCEEDED",
    "RECHAZADA": "DECLINED",
    "FALLIDA": "FAILED",
    "VENCIDA": "EXPIRED",
}
FONDOS_ALTERNA = {"CREDITO": "credit", "DEBITO": "debit"}
ESTATICOS = Path(__file__).parent / "estaticos"


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudSesionPago(BaseModel):
    referencia_comercio: str  # idempotente: la misma referencia devuelve la misma sesión
    monto: str  # decimal como texto
    moneda: str
    descripcion: str
    url_retorno: str
    idioma: str = "es"


class SolicitudSesionAlterna(BaseModel):
    reference: str
    amount: str
    currency: str
    description: str
    return_url: str
    locale: str = "es"


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


def _origenes(lista: str) -> set[tuple[str, str]]:
    return {
        (partes.scheme, partes.netloc)
        for partes in (urlsplit(url.strip()) for url in lista.split(","))
        if partes.scheme and partes.netloc
    }


def create_app(reloj: Callable[[], datetime] = lambda: datetime.now(UTC)) -> FastAPI:
    app = FastAPI(title="solventa-stub-pasarela", version="0.2.0")
    estado = {"modo": "sano"}
    # Cobros registrados por `referencia_comercio`.
    cobros: dict[str, dict] = {}
    # Sesiones de pago por id, y su id por `referencia_comercio` (idempotencia).
    sesiones: dict[str, dict] = {}
    sesion_por_referencia: dict[str, str] = {}
    candado = asyncio.Lock()
    vigencia = timedelta(seconds=int(os.getenv("SESION_VIGENCIA_SEGUNDOS", "600")))
    url_publica = os.getenv("PASARELA_URL_PUBLICA", "http://localhost:8103").rstrip("/")
    # La página no debe servir de redirección abierta: solo vuelve a los orígenes del comercio.
    retornos_permitidos = _origenes(
        os.getenv("URLS_RETORNO_PERMITIDAS", "http://localhost:5173,http://localhost:8080")
    )
    app.mount("/checkout-estaticos", StaticFiles(directory=ESTATICOS), name="estaticos")

    @app.middleware("http")
    async def _cabeceras_de_seguridad(request: Request, call_next):
        respuesta = await call_next(request)
        if request.url.path.startswith("/checkout"):
            respuesta.headers["Content-Security-Policy"] = pagina.CSP
            respuesta.headers["X-Content-Type-Options"] = "nosniff"
            respuesta.headers["Referrer-Policy"] = "no-referrer"
            respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

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

    # --- Sesiones de pago y página alojada (W03-28) ---

    def _vigente(sesion: dict) -> dict:
        """Marca VENCIDA una sesión ABIERTA cuyo plazo pasó. Una vencida no se reabre: el comercio
        pide otra con otra referencia."""
        if sesion["estado"] == "ABIERTA" and reloj() >= sesion["vence_en"]:
            sesion["estado"] = "VENCIDA"
        return sesion

    def _url_regreso(sesion: dict) -> str:
        separador = "&" if "?" in sesion["url_retorno"] else "?"
        return f"{sesion['url_retorno']}{separador}sesion={sesion['sesion_id']}"

    def _publica(sesion: dict) -> dict:
        cobrada = sesion["estado"] == "COBRADA"
        medio = sesion["medio"]
        return {
            "sesion_id": sesion["sesion_id"],
            "referencia_comercio": sesion["referencia_comercio"],
            "estado": sesion["estado"],
            "motivo": sesion["motivo"],
            "monto": sesion["monto"],
            "moneda": sesion["moneda"],
            "url_pago": f"{url_publica}/checkout/{sesion['sesion_id']}",
            "vence_en": sesion["vence_en"].isoformat(),
            # Solo el token, la marca y el tipo: nunca el número ni los últimos dígitos.
            "medio": (
                {"token": medio.token, "marca": medio.marca, "tipo": medio.tipo}
                if cobrada and medio
                else None
            ),
            "referencia": sesion["referencia_cobro"] if cobrada else None,
        }

    async def _crear_sesion(
        referencia: str, monto: str, moneda: str, descripcion: str, url_retorno: str, idioma: str
    ) -> dict:
        partes = urlsplit(url_retorno)
        if (partes.scheme, partes.netloc) not in retornos_permitidos:
            raise HTTPException(status_code=422, detail="url_retorno_no_permitida")
        try:
            if Decimal(monto) <= 0:
                raise InvalidOperation
        except InvalidOperation:
            raise HTTPException(status_code=422, detail="monto_invalido") from None
        async with candado:
            existente = sesion_por_referencia.get(referencia)
            if existente is not None:
                return sesiones[existente]
            sesion_id = f"ses_{uuid.uuid4().hex}"
            sesion = {
                "sesion_id": sesion_id,
                "referencia_comercio": referencia,
                "monto": monto,
                "moneda": moneda,
                "descripcion": descripcion[:120],
                "url_retorno": url_retorno,
                "idioma": pagina.idioma_valido(idioma),
                "estado": "ABIERTA",
                "motivo": None,
                "vence_en": reloj() + vigencia,
                "medio": None,
                "referencia_cobro": None,
            }
            sesiones[sesion_id] = sesion
            sesion_por_referencia[referencia] = sesion_id
            return sesion

    @app.post("/v1/sesiones-pago", tags=["proveedor"], status_code=201)
    async def crear_sesion_pago(
        body: SolicitudSesionPago,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        sesion = await _crear_sesion(
            body.referencia_comercio,
            body.monto,
            body.moneda,
            body.descripcion,
            body.url_retorno,
            body.idioma,
        )
        await _esperar_respuesta(modo_efectivo)
        return _publica(sesion)

    @app.get("/v1/sesiones-pago/{sesion_id}", tags=["proveedor"])
    async def consultar_sesion_pago(
        sesion_id: str,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        await _aplicar_modo(_modo_efectivo(modo, x_modo_simulado))
        sesion = sesiones.get(sesion_id)
        if sesion is None:
            raise HTTPException(status_code=404, detail="sesion no encontrada")
        return _publica(_vigente(sesion))

    @app.get("/checkout/{sesion_id}", tags=["pagina"], response_class=HTMLResponse)
    def pagina_pago(sesion_id: str, idioma: str | None = None) -> HTMLResponse:
        sesion = sesiones.get(sesion_id)
        if sesion is None:
            return HTMLResponse(
                "<!doctype html><title>404</title><p>Sesión no encontrada.</p>", 404
            )
        _vigente(sesion)
        return HTMLResponse(
            pagina.render(
                sesion,
                pagina.idioma_valido(idioma or sesion["idioma"]),
                _url_regreso(sesion),
                vencida=sesion["estado"] == "VENCIDA",
            )
        )

    @app.post("/checkout/{sesion_id}/pagar", tags=["pagina"])
    async def pagar(
        sesion_id: str,
        request: Request,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> JSONResponse:
        """Lo envía la propia página. El cuerpo se lee a mano y nunca se devuelve ni se registra:
        un 422 automático de FastAPI repetiría la tarjeta en la respuesta."""
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        sesion = sesiones.get(sesion_id)
        if sesion is None:
            return JSONResponse({"estado": None, "motivo": "sesion_no_encontrada"}, 404)
        try:
            datos = await request.json()
        except ValueError:
            datos = None
        if not isinstance(datos, dict):
            return JSONResponse({"campo": "numero"}, 422)
        if modo_efectivo == "caido":
            await asyncio.sleep(60)
        async with candado:
            _vigente(sesion)
            if sesion["estado"] != "ABIERTA":
                return JSONResponse({"estado": sesion["estado"], "motivo": sesion["motivo"]}, 409)
            try:
                numero, tipo = tarjetas.validar(datos, reloj().date())
            except tarjetas.TarjetaInvalida as error:
                return JSONResponse({"campo": error.campo}, 422)
            resultado, motivo = tarjetas.resultado_de(numero)
            if modo_efectivo == "error":
                resultado, motivo = "fallido", "pasarela_no_disponible"
            elif modo_efectivo == "rechaza":
                resultado, motivo = "rechazado", MOTIVO_RECHAZO
            medio = tarjetas.tokenizar(numero, tipo)
            del numero, datos  # la tarjeta no sale de esta función
            if resultado == "aprobado":
                sesion.update(estado="COBRADA", medio=medio, referencia_cobro=f"pg-{uuid.uuid4()}")
                cobros[sesion["referencia_comercio"]] = {
                    "estado": "cobrado",
                    "referencia": sesion["referencia_cobro"],
                    "motivo": None,
                    "detalle": {"referencia_comercio": sesion["referencia_comercio"]},
                }
            elif resultado == "rechazado":
                sesion.update(estado="RECHAZADA", motivo=motivo)
                cobros[sesion["referencia_comercio"]] = {
                    "estado": "rechazado",
                    "referencia": None,
                    "motivo": motivo,
                    "detalle": {"referencia_comercio": sesion["referencia_comercio"]},
                }
            else:
                sesion.update(estado="FALLIDA", motivo=motivo)
        # Con `lento` el cobro ya quedó hecho antes de esperar (lo detecta la conciliación).
        await _esperar_respuesta(modo_efectivo)
        return JSONResponse({"estado": sesion["estado"], "motivo": sesion["motivo"]})

    def _a_sesion_alterna(sesion: dict) -> dict:
        publica = _publica(sesion)
        medio = publica["medio"]
        return {
            "id": publica["sesion_id"],
            "reference": publica["referencia_comercio"],
            "status": ESTADOS_SESION_ALTERNA[publica["estado"]],
            "decline_code": publica["motivo"],
            "checkout_url": publica["url_pago"],
            "expires_at": publica["vence_en"],
            "payment_method": (
                {
                    "token": medio["token"],
                    "brand": medio["marca"],
                    "funding": FONDOS_ALTERNA[medio["tipo"]],
                }
                if medio
                else None
            ),
            "transaction_id": publica["referencia"],
        }

    @app.post("/alterna/v2/checkout-sessions", tags=["proveedor-alterno"], status_code=201)
    async def crear_sesion_alterna(
        body: SolicitudSesionAlterna,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        sesion = await _crear_sesion(
            body.reference,
            body.amount,
            body.currency,
            body.description,
            body.return_url,
            body.locale,
        )
        await _esperar_respuesta(modo_efectivo)
        return _a_sesion_alterna(sesion)

    @app.get("/alterna/v2/checkout-sessions/{sesion_id}", tags=["proveedor-alterno"])
    async def consultar_sesion_alterna(
        sesion_id: str,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        await _aplicar_modo(_modo_efectivo(modo, x_modo_simulado))
        sesion = sesiones.get(sesion_id)
        if sesion is None:
            raise HTTPException(status_code=404, detail="checkout_session_not_found")
        return _a_sesion_alterna(_vigente(sesion))

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
