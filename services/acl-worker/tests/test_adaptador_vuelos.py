"""Pruebas de contrato del adaptador de vuelos (KAN-259, Fase 3)."""

import httpx
import pytest

from app.adapters.vuelos import AdaptadorVuelo
from app.domain.puertos import ConsultaVuelo

CONSULTA = ConsultaVuelo(numero_vuelo="AV9340", fecha="2026-11-11")


def _adaptador(handler) -> AdaptadorVuelo:
    cliente = httpx.Client(
        base_url="http://stub-eventos-parametricos", transport=httpx.MockTransport(handler)
    )
    return AdaptadorVuelo("http://stub-eventos-parametricos", "clave-prueba", 0.7, cliente=cliente)


def test_envia_la_llave_y_el_numero_de_vuelo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/flights"
        params = dict(httpx.QueryParams(request.url.query.decode()))
        assert params["access_key"] == "clave-prueba"
        assert params["flight_iata"] == "AV9340"
        assert params["flight_date"] == "2026-11-11"
        return httpx.Response(
            200,
            json={"data": [{"flight_status": "active", "departure": {"delay": 145}}]},
        )

    medicion = _adaptador(handler).consultar(CONSULTA)
    assert medicion.encontrado is True
    assert medicion.retraso_minutos == 145
    assert medicion.cancelado is False
    assert medicion.fuente == "aviationstack"


def test_un_vuelo_cancelado_se_marca_aunque_el_retraso_sea_cero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"flight_status": "cancelled", "departure": {"delay": 0}}]}
        )

    medicion = _adaptador(handler).consultar(CONSULTA)
    assert medicion.cancelado is True
    assert medicion.retraso_minutos == 0


def test_un_vuelo_sin_datos_es_una_respuesta_valida_no_una_falla() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    medicion = _adaptador(handler).consultar(CONSULTA)
    assert medicion.encontrado is False


def test_un_5xx_es_una_falla() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "proveedor_no_disponible"})

    with pytest.raises(httpx.HTTPStatusError):
        _adaptador(handler).consultar(CONSULTA)


def test_la_sonda_usa_un_vuelo_sintetico_y_no_falla_si_no_existe() -> None:
    vistas = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request.url)
        return httpx.Response(200, json={"data": []})

    _adaptador(handler).sondear()  # no debe lanzar
    assert len(vistas) == 1
