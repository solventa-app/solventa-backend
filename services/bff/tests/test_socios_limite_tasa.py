"""Límite de tasa por credencial de socio (KAN-217, contrato v0.61)."""

import asyncio
from dataclasses import replace

import pytest
from test_socios_api import COTIZACION, Entorno, _es_error_publico

from app.socios import limite_tasa
from app.socios.limite_tasa import AlmacenVentanasMemoria, LimiteTasa


class _Entorno(Entorno):
    def __init__(self, produccion: int = 3, pruebas: int = 2) -> None:
        super().__init__(presupuesto=100)
        self.config = replace(
            self.config, limite_socio_rps_produccion=produccion, limite_socio_rps_pruebas=pruebas
        )


@pytest.fixture
def e() -> _Entorno:
    return _Entorno()


def _congelar(client, segundo: list[int]) -> None:
    client.app.state.limite_tasa_socios._reloj = lambda: segundo[0]


def test_pasar_el_tope_en_el_mismo_segundo_es_429(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        codigos = [client.get("/v1/consumo", headers=e.cabeceras()).status_code for _ in range(3)]
        excedida = client.get("/v1/consumo", headers=e.cabeceras())
        en_ingles = client.get("/v1/consumo", headers={**e.cabeceras(), "Accept-Language": "en"})

    assert codigos == [200, 200, 200]
    cuerpo = _es_error_publico(excedida, 429, "LIMITE_DE_TASA")
    assert cuerpo["puedeReintentar"] is True
    assert excedida.headers["retry-after"] == "1"
    assert _es_error_publico(en_ingles, 429, "LIMITE_DE_TASA")["mensaje"] == (
        "Too many requests per second for this credential. Please try again."
    )


def test_el_segundo_siguiente_vuelve_a_pasar(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        for _ in range(4):
            client.get("/v1/consumo", headers=e.cabeceras())
        segundo[0] += 1
        siguiente = client.get("/v1/consumo", headers=e.cabeceras())

    assert siguiente.status_code == 200


def test_el_tope_es_por_credencial(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        for _ in range(4):
            client.get("/v1/consumo", headers=e.cabeceras())
        otro_socio = client.get("/v1/consumo", headers=e.cabeceras(socio="socio-2"))
        otro_entorno = client.get("/v1/consumo", headers=e.cabeceras(entorno="PRUEBAS"))

    assert otro_socio.status_code == 200
    assert otro_entorno.status_code == 200


def test_pruebas_tiene_su_propio_tope(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        pruebas = e.cabeceras(entorno="PRUEBAS")
        codigos = [client.get("/v1/consumo", headers=pruebas).status_code for _ in range(3)]

    assert codigos == [200, 200, 429]


def test_un_429_no_gasta_presupuesto_ni_llega_a_rating(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        for _ in range(3):
            client.get("/v1/consumo", headers=e.cabeceras())
        cotizar = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    _es_error_publico(cotizar, 429, "LIMITE_DE_TASA")
    assert e.socios.consumidas == {}


def test_las_rutas_publicas_no_cuentan(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        for _ in range(10):
            client.get("/v1/openapi.json")
        consumo = client.get("/v1/consumo", headers=e.cabeceras())

    assert consumo.status_code == 200


def test_el_429_queda_en_el_consumo_como_error_del_cliente(e) -> None:
    segundo = [1000]
    with e.cliente() as client:
        _congelar(client, segundo)
        codigos = [
            client.get(f"/v1/propuestas/p-{i}", headers=e.cabeceras()).status_code for i in range(4)
        ]

    assert codigos == [404, 404, 404, 429]
    propuestas = [c for c in e.socios.consumos if c[2] == "propuestas"]
    assert len(propuestas) == 4 and {c[3] for c in propuestas} == {"ERROR_CLIENTE"}


class _AlmacenCaido:
    async def contar(self, clave: str, ttl: int) -> int:
        raise ConnectionError("redis")


def test_sin_redis_deja_pasar_y_lo_cuenta() -> None:
    limite = LimiteTasa(_AlmacenCaido(), {"PRODUCCION": 1, "PRUEBAS": 1})
    antes = limite_tasa.SIN_ALMACEN._value.get()

    async def correr() -> None:
        for _ in range(5):
            await limite.revisar("s", "PRODUCCION")

    asyncio.run(correr())
    assert limite_tasa.SIN_ALMACEN._value.get() == antes + 5


def test_tope_cero_es_sin_limite() -> None:
    limite = LimiteTasa(AlmacenVentanasMemoria(), {"PRODUCCION": 0, "PRUEBAS": 0})

    async def correr() -> None:
        for _ in range(100):
            await limite.revisar("s", "PRODUCCION")

    asyncio.run(correr())
