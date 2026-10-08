"""Pruebas de contrato del adaptador de la pasarela de pagos (T-W05-2)."""

import json
import time

import httpx
import pytest

from app.adapters.pasarela import AdaptadorPasarela
from app.domain.puertos import OrdenCobro

ORDEN = OrdenCobro(
    clave_idempotencia="idem-1",
    poliza_id="pol-1",
    monto="150000.00",
    moneda="COP",
    token_medio_pago="tok_abc123",
)


def _cliente_mock(handler) -> httpx.Client:
    return httpx.Client(base_url="http://stub-pasarela", transport=httpx.MockTransport(handler))


def test_mapea_cobro_exitoso() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/cobros"
        cuerpo = json.loads(request.content)
        assert cuerpo["token_medio_pago"] == "tok_abc123"
        assert "monto" not in cuerpo or cuerpo["monto"] == "150000.00"
        return httpx.Response(200, json={"estado": "cobrado", "referencia": "pg-123"})

    adaptador = AdaptadorPasarela("http://stub-pasarela", 0.7, cliente=_cliente_mock(handler))
    resultado = adaptador.cobrar(ORDEN)

    assert resultado.estado == "cobrado"
    assert resultado.referencia == "pg-123"


def test_propaga_error_5xx_como_excepcion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "pasarela_no_disponible"})

    adaptador = AdaptadorPasarela("http://stub-pasarela", 0.7, cliente=_cliente_mock(handler))
    with pytest.raises(httpx.HTTPStatusError):
        adaptador.cobrar(ORDEN)


def test_sondear_usa_el_endpoint_de_estado_no_un_cobro_real() -> None:
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    adaptador = AdaptadorPasarela("http://stub-pasarela", 0.7, cliente=_cliente_mock(handler))
    adaptador.sondear()

    assert llamadas == ["/v1/estado"]


def test_respeta_el_timeout_duro_con_proveedor_caido(servidor_caido: str) -> None:
    adaptador = AdaptadorPasarela(servidor_caido, timeout_s=0.1)
    inicio = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        adaptador.cobrar(ORDEN)
    assert time.monotonic() - inicio < 0.5
