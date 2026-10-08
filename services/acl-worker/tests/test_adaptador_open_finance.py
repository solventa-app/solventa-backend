"""Pruebas de contrato del adaptador de Open Finance (T-W01-8)."""

import json
import time

import httpx
import pytest

from app.adapters.open_finance import AdaptadorOpenFinance
from app.domain.puertos import ConsultaFuente

CONSULTA = ConsultaFuente(cliente_id="c-1", consentimiento_id="k-1", fuente="cuentas-bancarias")


def _cliente_mock(handler) -> httpx.Client:
    return httpx.Client(base_url="http://stub-open-finance", transport=httpx.MockTransport(handler))


def test_mapea_respuesta_exitosa() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/fuentes/cuentas-bancarias/consulta"
        assert json.loads(request.content) == {"cliente_id": "c-1"}
        return httpx.Response(
            200, json={"fuente": "cuentas-bancarias", "datos": {"saldo": 100}}
        )

    cliente = _cliente_mock(handler)
    adaptador = AdaptadorOpenFinance("http://stub-open-finance", 0.7, cliente=cliente)
    datos = adaptador.consultar(CONSULTA)

    assert datos.fuente == "cuentas-bancarias"
    assert datos.datos == {"saldo": 100}
    assert datos.de_cache is False
    assert datos.degradado is False


def test_propaga_error_5xx_como_excepcion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "proveedor_no_disponible"})

    cliente = _cliente_mock(handler)
    adaptador = AdaptadorOpenFinance("http://stub-open-finance", 0.7, cliente=cliente)
    with pytest.raises(httpx.HTTPStatusError):
        adaptador.consultar(CONSULTA)


def test_respeta_el_timeout_duro_con_proveedor_caido(servidor_caido: str) -> None:
    adaptador = AdaptadorOpenFinance(servidor_caido, timeout_s=0.1)
    inicio = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        adaptador.consultar(CONSULTA)
    duracion_s = time.monotonic() - inicio
    assert duracion_s < 0.5  # nunca espera el timeout "de fábrica" de httpx (5s)


def test_timeout_por_llamada_tiene_prioridad_sobre_el_de_fabrica(servidor_lento: str) -> None:
    """El servidor responde a los 300ms: con timeout por defecto amplio (1s)
    pasaría, pero si la llamada pide 100ms (p. ej. el presupuesto restante de
    un reintento) debe fallar igual, sin importar el timeout del constructor."""
    adaptador = AdaptadorOpenFinance(servidor_lento, timeout_s=1.0)
    with pytest.raises(httpx.TimeoutException):
        adaptador.consultar(CONSULTA, timeout_s=0.1)
