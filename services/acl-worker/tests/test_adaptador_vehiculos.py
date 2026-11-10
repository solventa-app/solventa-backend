"""Pruebas de contrato del adaptador de los datos del vehículo (KAN-290)."""

import httpx
import pytest

from app.adapters.vehiculos import AdaptadorVehiculos
from app.application.fuentes_config import fuentes_vehiculo
from app.domain.puertos import ConsultaFuente

CONSULTA = ConsultaFuente(
    cliente_id="hmac-placa", consentimiento_id="k-1", fuente="runt-vehiculo", placa="ABC123"
)


def _adaptador(handler) -> AdaptadorVehiculos:
    cliente = httpx.Client(base_url="http://stub-vehiculos", transport=httpx.MockTransport(handler))
    return AdaptadorVehiculos("http://stub-vehiculos", 0.7, cliente=cliente)


def test_las_fuentes_salen_del_contrato() -> None:
    assert fuentes_vehiculo() == ("runt-vehiculo", "fasecolda-valor", "siniestralidad-placa")


def test_envia_la_placa_y_no_la_devuelve_en_los_datos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/fuentes/runt-vehiculo/consulta"
        assert request.read() == b'{"placa":"ABC123"}'
        return httpx.Response(200, json={"datos": {"placa": "ABC123", "marca": "MAZDA"}})

    datos = _adaptador(handler).consultar(CONSULTA)
    assert datos.datos == {"encontrada": True, "marca": "MAZDA"}
    assert "ABC123" not in str(datos.datos)


def test_placa_inexistente_es_un_dato_no_una_falla() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "placa_no_encontrada"})

    datos = _adaptador(handler).consultar(CONSULTA)
    assert datos.datos == {"encontrada": False}
    assert datos.degradado is False


def test_un_5xx_y_un_404_de_ruta_son_fallas() -> None:
    for respuesta in (
        httpx.Response(503, json={"detail": "proveedor_no_disponible"}),
        httpx.Response(404, json={"detail": "fuente desconocida: x"}),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            _adaptador(lambda _r, r=respuesta: r).consultar(CONSULTA)


def test_sin_placa_no_consulta() -> None:
    sin_placa = ConsultaFuente(cliente_id="x", consentimiento_id="k", fuente="runt-vehiculo")
    with pytest.raises(ValueError):
        _adaptador(lambda _r: httpx.Response(200, json={})).consultar(sin_placa)


def test_la_sonda_usa_una_placa_sintetica() -> None:
    vistas = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request.read())
        return httpx.Response(200, json={"datos": {}})

    _adaptador(handler).sondear()
    assert vistas == [b'{"placa":"AAA000"}']
