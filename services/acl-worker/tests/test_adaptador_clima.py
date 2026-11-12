"""Pruebas de contrato del adaptador de clima (KAN-259, Fase 3)."""

import httpx
import pytest

from app.adapters.clima import AdaptadorClima, SerieClimaVacia, VariableClimaDesconocida
from app.domain.puertos import ConsultaClima

CONSULTA = ConsultaClima(
    clave="4.61,-74.08", latitud=4.61, longitud=-74.08, variable="PRECIPITACION_MM"
)


def _adaptador(handler) -> AdaptadorClima:
    cliente = httpx.Client(
        base_url="http://stub-eventos-parametricos", transport=httpx.MockTransport(handler)
    )
    return AdaptadorClima("http://stub-eventos-parametricos", 0.7, cliente=cliente)


def test_consulta_pide_el_parametro_correcto_y_suma_la_precipitacion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/forecast"
        params = dict(httpx.QueryParams(request.url.query.decode()))
        assert params["latitude"] == "4.61"
        assert params["hourly"] == "precipitation"
        return httpx.Response(200, json={"hourly": {"precipitation": [1.0, 2.0, 3.0]}})

    medicion = _adaptador(handler).consultar(CONSULTA)
    assert medicion.valor == 6.0
    assert medicion.fuente == "open-meteo"


def test_viento_y_temperatura_toman_el_pico_de_la_serie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hourly": {"wind_speed_10m": [5.0, 40.0, 9.0]}})

    consulta = ConsultaClima(clave="x", latitud=0, longitud=0, variable="VIENTO_KMH")
    medicion = _adaptador(handler).consultar(consulta)
    assert medicion.valor == 40.0


def test_una_serie_vacia_es_una_falla() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hourly": {"precipitation": []}})

    with pytest.raises(SerieClimaVacia):
        _adaptador(handler).consultar(CONSULTA)


def test_un_5xx_es_una_falla() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "proveedor_no_disponible"})

    with pytest.raises(httpx.HTTPStatusError):
        _adaptador(handler).consultar(CONSULTA)


def test_una_variable_desconocida_no_llama_al_proveedor() -> None:
    llamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return httpx.Response(200, json={})

    consulta = ConsultaClima(clave="x", latitud=0, longitud=0, variable="NO_EXISTE")  # type: ignore[arg-type]
    with pytest.raises(VariableClimaDesconocida):
        _adaptador(handler).consultar(consulta)
    assert llamadas == []


def test_la_sonda_consulta_una_celda_sintetica() -> None:
    vistas = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request.url)
        return httpx.Response(200, json={"hourly": {"precipitation": [1.0]}})

    _adaptador(handler).sondear()
    assert len(vistas) == 1
