"""Pruebas de contrato del adaptador de Open Data (T-W01-9)."""

import time

import httpx
import pytest

from app.adapters.open_data import AdaptadorOpenData
from app.domain.puertos import ConsultaFuente

CONSULTA = ConsultaFuente(cliente_id="c-1", consentimiento_id="k-1", fuente="camara-comercio")


def _cliente_mock(handler) -> httpx.Client:
    return httpx.Client(base_url="http://stub-open-data", transport=httpx.MockTransport(handler))


def test_mapea_respuesta_exitosa() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/fuentes/camara-comercio/consulta"
        return httpx.Response(
            200, json={"fuente": "camara-comercio", "datos": {"existe_registro": True}}
        )

    adaptador = AdaptadorOpenData("http://stub-open-data", 0.7, cliente=_cliente_mock(handler))
    datos = adaptador.consultar(CONSULTA)

    assert datos.fuente == "camara-comercio"
    assert datos.datos == {"existe_registro": True}
    assert datos.de_cache is False
    assert datos.degradado is False


def test_propaga_error_5xx_como_excepcion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "proveedor_no_disponible"})

    adaptador = AdaptadorOpenData("http://stub-open-data", 0.7, cliente=_cliente_mock(handler))
    with pytest.raises(httpx.HTTPStatusError):
        adaptador.consultar(CONSULTA)


def test_respeta_el_timeout_duro_con_proveedor_caido(servidor_caido: str) -> None:
    adaptador = AdaptadorOpenData(servidor_caido, timeout_s=0.1)
    inicio = time.monotonic()
    with pytest.raises(httpx.TimeoutException):
        adaptador.consultar(CONSULTA)
    assert time.monotonic() - inicio < 0.5
