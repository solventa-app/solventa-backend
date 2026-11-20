"""Antifraude mínimo de la API de socios (v0.60, HA-SEG-004, KAN-66): el sondeo con 403/404
suspende la credencial 15 minutos."""

import asyncio

import pytest
from test_socios_api import COTIZACION, Entorno, _es_error_publico

from app.socios import antifraude
from app.socios.antifraude import (
    UMBRAL_SONDEO,
    AlmacenContadoresMemoria,
    Antifraude,
)

MAL = {"clientId": "sol_prod_1", "clave": "otra"}
BIEN = {"clientId": "sol_prod_1", "clave": "Clave-prod-1"}


@pytest.fixture
def e() -> Entorno:
    return Entorno()


def _almacen(client) -> AlmacenContadoresMemoria:
    return client.app.state.antifraude_socios._almacen


def test_el_token_sigue_respondiendo_siempre_credenciales_invalidas(e) -> None:
    """La fuerza bruta la frena AUTH sin revelar nada: el BFF no agrega otro error."""
    with e.cliente() as client:
        codigos = {client.post("/v1/token", json=MAL).status_code for _ in range(40)}
        bien = client.post("/v1/token", json=BIEN)

    assert codigos == {401}
    assert bien.status_code == 200


def test_la_suspension_vence_sola(e) -> None:
    with e.cliente() as client:
        for i in range(UMBRAL_SONDEO):
            client.get(f"/v1/propuestas/x-{i}", headers=e.cabeceras())
        suspendida = client.get("/v1/consumo", headers=e.cabeceras())
        almacen = _almacen(client)
        base = almacen.ahora()
        almacen.ahora = lambda: base + 15 * 60 + 1
        despues = client.get("/v1/consumo", headers=e.cabeceras())

    cuerpo = _es_error_publico(suspendida, 403, "CREDENCIAL_SUSPENDIDA")
    assert cuerpo["puedeReintentar"] is False
    assert 0 < int(suspendida.headers["retry-after"]) <= 15 * 60
    assert despues.status_code == 200


def test_el_mensaje_va_en_ingles(e) -> None:
    with e.cliente() as client:
        for i in range(UMBRAL_SONDEO):
            client.get(f"/v1/propuestas/x-{i}", headers=e.cabeceras())
        en = client.get("/v1/consumo", headers={**e.cabeceras(), "Accept-Language": "en"})

    assert _es_error_publico(en, 403, "CREDENCIAL_SUSPENDIDA")["mensaje"].startswith(
        "The credential was temporarily suspended"
    )


def test_sondeo_con_404_suspende_la_credencial_de_ese_entorno(e) -> None:
    with e.cliente() as client:
        codigos = [
            client.get(f"/v1/propuestas/no-es-mia-{i}", headers=e.cabeceras()).status_code
            for i in range(UMBRAL_SONDEO)
        ]
        consumo = client.get("/v1/consumo", headers=e.cabeceras())
        cotizar = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras())
        en_pruebas = client.get("/v1/consumo", headers=e.cabeceras(entorno="PRUEBAS"))

    assert codigos == [404] * UMBRAL_SONDEO
    _es_error_publico(consumo, 403, "CREDENCIAL_SUSPENDIDA")
    _es_error_publico(cotizar, 403, "CREDENCIAL_SUSPENDIDA")
    assert e.socios.consumidas == {}, "suspendida no gasta presupuesto"
    assert en_pruebas.status_code == 200, "el otro entorno sigue"


def test_las_respuestas_de_la_suspension_no_la_alargan(e) -> None:
    with e.cliente() as client:
        for i in range(UMBRAL_SONDEO):
            client.get(f"/v1/propuestas/x-{i}", headers=e.cabeceras())
        almacen = _almacen(client)
        base = almacen.ahora()
        for _ in range(UMBRAL_SONDEO):
            client.get("/v1/consumo", headers=e.cabeceras())
        almacen.ahora = lambda: base + 15 * 60 + 1
        despues = client.get("/v1/consumo", headers=e.cabeceras())

    assert despues.status_code == 200


def test_el_uso_normal_no_suspende(e) -> None:
    with e.cliente() as client:
        codigos = {
            client.get("/v1/consumo", headers=e.cabeceras()).status_code
            for _ in range(UMBRAL_SONDEO + 5)
        }

    assert codigos == {200}


class _AlmacenCaido:
    async def incrementar(self, clave: str, ttl: int) -> int:
        raise ConnectionError("redis")

    async def suspender(self, clave: str, ttl: int) -> None:
        raise ConnectionError("redis")

    async def restante(self, clave: str) -> int:
        raise ConnectionError("redis")


def test_sin_redis_deja_pasar() -> None:
    af = Antifraude(_AlmacenCaido())
    antes = antifraude.FALLAS_ALMACEN._value.get()

    async def correr() -> None:
        await af.revisar_credencial("s", "PRODUCCION")
        await af.respuesta_rechazada("s", "PRODUCCION")

    asyncio.run(correr())
    assert antifraude.FALLAS_ALMACEN._value.get() == antes + 2


def test_cuenta_la_anomalia_una_vez() -> None:
    almacen = AlmacenContadoresMemoria()
    af = Antifraude(almacen)
    antes = antifraude.ANOMALIAS.labels(regla="SONDEO")._value.get()

    async def correr() -> None:
        for _ in range(UMBRAL_SONDEO * 2):
            await af.respuesta_rechazada("s", "PRODUCCION")

    asyncio.run(correr())
    assert antifraude.ANOMALIAS.labels(regla="SONDEO")._value.get() == antes + 1
