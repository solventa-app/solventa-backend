"""Antifraude mínimo de la API de socios (v0.60, HA-SEG-004, KAN-66): fuerza bruta en el token y
sondeo con 403/404, cada uno con su suspensión temporal de 15 minutos."""

import asyncio

import pytest
from test_socios_api import COTIZACION, Entorno, _es_error_publico

from app.socios import antifraude
from app.socios.antifraude import (
    UMBRAL_SONDEO,
    UMBRAL_TOKEN,
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


def test_fuerza_bruta_suspende_el_client_id_aunque_la_clave_sea_correcta(e) -> None:
    with e.cliente() as client:
        fallidos = [client.post("/v1/token", json=MAL).status_code for _ in range(UMBRAL_TOKEN)]
        con_la_buena = client.post("/v1/token", json=BIEN)
        en_ingles = client.post("/v1/token", json=BIEN, headers={"Accept-Language": "en"})

    assert fallidos == [401] * UMBRAL_TOKEN
    cuerpo = _es_error_publico(con_la_buena, 403, "CREDENCIAL_SUSPENDIDA")
    assert cuerpo["puedeReintentar"] is False
    assert 0 < int(con_la_buena.headers["retry-after"]) <= 15 * 60
    assert _es_error_publico(en_ingles, 403, "CREDENCIAL_SUSPENDIDA")["mensaje"].startswith(
        "The credential was temporarily suspended"
    )


def test_menos_fallidos_que_el_umbral_no_suspende(e) -> None:
    with e.cliente() as client:
        for _ in range(UMBRAL_TOKEN - 1):
            client.post("/v1/token", json=MAL)
        bien = client.post("/v1/token", json=BIEN)

    assert bien.status_code == 200


def test_la_suspension_del_token_es_por_client_id(e) -> None:
    with e.cliente() as client:
        for _ in range(UMBRAL_TOKEN):
            client.post("/v1/token", json={"clientId": "otro-cliente", "clave": "x"})
        bien = client.post("/v1/token", json=BIEN)

    assert bien.status_code == 200


def test_la_suspension_vence_sola(e) -> None:
    with e.cliente() as client:
        for _ in range(UMBRAL_TOKEN):
            client.post("/v1/token", json=MAL)
        almacen = _almacen(client)
        base = almacen.ahora()
        almacen.ahora = lambda: base + 15 * 60 + 1
        bien = client.post("/v1/token", json=BIEN)

    assert bien.status_code == 200


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
        await af.revisar_client_id("c")
        await af.token_fallido("c")
        await af.revisar_credencial("s", "PRODUCCION")
        await af.respuesta_rechazada("s", "PRODUCCION")

    asyncio.run(correr())
    assert antifraude.FALLAS_ALMACEN._value.get() == antes + 4


def test_la_clave_del_token_no_guarda_el_client_id() -> None:
    almacen = AlmacenContadoresMemoria()
    asyncio.run(Antifraude(almacen).token_fallido("sol_prod_1"))
    assert all("sol_prod_1" not in clave for clave in almacen._contadores)


def test_cuenta_la_anomalia_una_vez() -> None:
    almacen = AlmacenContadoresMemoria()
    af = Antifraude(almacen)
    antes = antifraude.ANOMALIAS.labels(regla="SONDEO")._value.get()

    async def correr() -> None:
        for _ in range(UMBRAL_SONDEO * 2):
            await af.respuesta_rechazada("s", "PRODUCCION")

    asyncio.run(correr())
    assert antifraude.ANOMALIAS.labels(regla="SONDEO")._value.get() == antes + 1
