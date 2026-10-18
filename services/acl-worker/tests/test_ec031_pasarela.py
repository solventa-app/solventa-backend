"""EC031 / HA-MOD-001 / CA-W05-06: cambiar de pasarela es agregar un adaptador.

Los dos adaptadores cumplen el MISMO contrato del puerto: las mismas pruebas, parametrizadas,
corren contra cada uno. Y el caso de uso (`ServicioCobro`, con su Circuit Breaker y sus
reintentos) funciona igual con cualquiera: no sabe qué proveedor hay detrás."""

import json

import httpx
import pytest

from app.adapters.pasarela import AdaptadorPasarela
from app.adapters.pasarela_alterna import AdaptadorPasarelaAlterna
from app.application.circuitos import NOMBRE_CIRCUITO_PASARELA, construir_fabrica
from app.application.servicio_pagos import ServicioCobro
from app.domain.puertos import OrdenCobro

ORDEN = OrdenCobro(
    id_intento_cobro="idem-77",
    poliza_id="pol-1",
    monto="150000.00",
    moneda="COP",
    token_medio_pago="tok_abc123",
)


def _estandar(cobros: dict[str, dict], fallar: bool = False) -> AdaptadorPasarela:
    def handler(request: httpx.Request) -> httpx.Response:
        if fallar:
            return httpx.Response(503)
        if request.method == "POST" and request.url.path == "/v1/cobros":
            referencia = json.loads(request.content)["referencia_comercio"]
            cobros.setdefault(
                referencia, {"estado": "cobrado", "referencia": "pg-1", "motivo": None}
            )
            return httpx.Response(200, json=cobros[referencia])
        if request.url.path.startswith("/v1/cobros/"):
            encontrado = cobros.get(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json=encontrado) if encontrado else httpx.Response(404)
        return httpx.Response(200, json={"ok": True})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    return AdaptadorPasarela("http://p", 0.7, cliente=cliente)


def _alterna(cobros: dict[str, dict], fallar: bool = False) -> AdaptadorPasarelaAlterna:
    estados = {"cobrado": "APPROVED", "rechazado": "DECLINED", "pendiente": "PENDING"}

    def a_alterna(referencia: str, cobro: dict) -> dict:
        return {
            "status": estados[cobro["estado"]],
            "transaction_id": cobro["referencia"],
            "decline_code": cobro["motivo"],
            "reference": referencia,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if fallar:
            return httpx.Response(503)
        if request.method == "POST" and request.url.path == "/alterna/v2/charges":
            referencia = json.loads(request.content)["reference"]
            cobros.setdefault(
                referencia, {"estado": "cobrado", "referencia": "pg-1", "motivo": None}
            )
            return httpx.Response(200, json=a_alterna(referencia, cobros[referencia]))
        if request.url.path.startswith("/alterna/v2/charges/"):
            referencia = request.url.path.rsplit("/", 1)[1]
            encontrado = cobros.get(referencia)
            if encontrado is None:
                return httpx.Response(404)
            return httpx.Response(200, json=a_alterna(referencia, encontrado))
        return httpx.Response(200, json={"ok": True})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    return AdaptadorPasarelaAlterna("http://p", 0.7, cliente=cliente)


FABRICAS = [pytest.param(_estandar, id="estandar"), pytest.param(_alterna, id="alterna")]


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_cobrar_devuelve_el_resultado_del_puerto(fabrica) -> None:
    resultado = fabrica({}).cobrar(ORDEN)

    assert (resultado.estado, resultado.referencia, resultado.motivo) == ("cobrado", "pg-1", None)


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_consultar_encontrado_y_no_encontrado(fabrica) -> None:
    adaptador = fabrica({})
    assert adaptador.consultar("idem-77") is None  # no es una falla

    adaptador.cobrar(ORDEN)

    assert adaptador.consultar("idem-77").estado == "cobrado"


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_un_5xx_se_propaga_para_el_circuit_breaker(fabrica) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        fabrica({}, fallar=True).cobrar(ORDEN)


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_sondear_no_crea_cobros(fabrica) -> None:
    cobros: dict[str, dict] = {}

    fabrica(cobros).sondear()

    assert cobros == {}


@pytest.mark.parametrize("fabrica", FABRICAS)
def test_el_caso_de_uso_funciona_igual_con_cualquier_adaptador(fabrica) -> None:
    """`ServicioCobro` es el mismo código: cobra, concilia y degrada si el proveedor cae."""
    cobros: dict[str, dict] = {}
    servicio = ServicioCobro(
        fabrica(cobros), construir_fabrica(3, 5), NOMBRE_CIRCUITO_PASARELA, presupuesto_s=0.7
    )

    assert servicio.cobrar(ORDEN).estado == "cobrado"
    assert servicio.consultar("idem-77").resultado == "encontrado"
    assert servicio.consultar("otra").resultado == "no_encontrado"

    caida = ServicioCobro(
        fabrica({}, fallar=True),
        construir_fabrica(3, 5),
        NOMBRE_CIRCUITO_PASARELA,
        presupuesto_s=0.7,
    )
    assert caida.cobrar(ORDEN).estado == "pendiente"  # regla 4: nunca un 5xx crudo


def test_alterna_traduce_los_estados_del_proveedor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "DECLINED", "transaction_id": None, "decline_code": "sin_fondos"}
        )

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    resultado = AdaptadorPasarelaAlterna("http://p", 0.7, cliente=cliente).cobrar(ORDEN)

    assert (resultado.estado, resultado.motivo) == ("rechazado", "sin_fondos")


def test_alterna_no_da_por_cobrado_un_estado_que_no_conoce() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "QUIEN_SABE"})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    resultado = AdaptadorPasarelaAlterna("http://p", 0.7, cliente=cliente).cobrar(ORDEN)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == "estado_desconocido_del_proveedor"


def test_alterna_envia_los_nombres_de_campos_del_proveedor_y_nunca_datos_de_tarjeta() -> None:
    enviado: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        enviado.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "APPROVED", "transaction_id": "t-1"})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    AdaptadorPasarelaAlterna("http://p", 0.7, cliente=cliente).cobrar(ORDEN)

    assert enviado[0] == {
        "reference": "idem-77",
        "policy": "pol-1",
        "amount": "150000.00",
        "currency": "COP",
        "payment_token": "tok_abc123",
    }
