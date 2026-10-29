"""Salud del sistema (HU-W07, bloque 6): fórmulas, reglas de color, caché, la vista del socio sin
micros, Prometheus y las dos salidas (`GET /v1/estado` y `estadoSistema`)."""

import asyncio
import json

import httpx
import pytest
from fakes import AclFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient
from test_socios_api import Entorno

from app.configuracion import Configuracion
from app.main import create_app
from app.salud import (
    Metricas,
    MetricasPrometheus,
    ServicioSalud,
    cargar_config,
    color,
    puntaje_latencia,
)

MICROS = [m["nombre"] for m in cargar_config()["micros"]]
CIRCUITOS = {n: "closed" for n in ("open-finance", "open-data", "firma", "kyc", "pasarela",
                                   "notificaciones")}  # fmt: skip


def _salud(caidos=(), circuitos=None, metricas=None, reloj=lambda: 0.0) -> ServicioSalud:
    async def sonda_de(nombre):
        return nombre not in caidos

    def sonda(nombre):
        return lambda: sonda_de(nombre)

    async def leer_circuitos():
        return dict(CIRCUITOS if circuitos is None else circuitos)

    return ServicioSalud({n: sonda(n) for n in MICROS}, leer_circuitos, metricas, reloj=reloj)


def _calcular(salud: ServicioSalud, ventana: str = "1h") -> dict:
    return asyncio.run(salud.calcular(ventana))


class MetricasFijas:
    def __init__(self, por_micro: dict[str, Metricas], borde: Metricas | None = None) -> None:
        self.por_micro = por_micro
        self._borde = borde

    async def micro(self, nombre, ventana):
        return self.por_micro.get(nombre)

    async def borde(self, ventana):
        return self._borde


@pytest.mark.parametrize(
    "media,esperado", [(None, 100), (100, 100), (120, 100), (180, 50), (240, 0), (500, 0)]
)
def test_puntaje_de_latencia(media, esperado) -> None:
    assert puntaje_latencia(media, 120) == esperado


def test_colores() -> None:
    umbrales = {"verde": 90, "amarillo": 70}
    assert [color(v, umbrales) for v in (95, 90, 80, 70, 69)] == [
        "VERDE", "VERDE", "AMARILLO", "AMARILLO", "ROJO",
    ]  # fmt: skip


def test_todo_sano_y_sin_metricas_es_100_en_verde() -> None:
    datos = _calcular(_salud())

    assert datos["porcentaje"] == 100 and datos["color"] == "VERDE"
    assert all(a["color"] == "VERDE" for a in datos["apis"])
    assert all(m["sin_trafico"] for m in datos["micros"])


def test_rating_caido_deja_cotizacion_en_rojo_y_la_barra_en_amarillo() -> None:
    """El defecto de la v2: con RATING caído la barra quedaba en 90 % y Cotización en 90, las dos
    en verde, aunque no se pudiera cotizar."""
    datos = _calcular(_salud(caidos={"rating"}))
    apis = {a["nombre"]: a for a in datos["apis"]}

    assert datos["porcentaje"] == 90
    assert datos["color"] == "AMARILLO"
    assert apis["COTIZACION"]["color"] == "ROJO"
    assert apis["EMISION"]["color"] == "VERDE"


def test_un_circuito_abierto_baja_la_api_que_lo_usa() -> None:
    datos = _calcular(_salud(circuitos={**CIRCUITOS, "pasarela": "opened", "kyc": "half-opened"}))
    emision = next(a for a in datos["apis"] if a["nombre"] == "EMISION")

    # micros 100; externas (100 + 50 + 0 + 100) / 4 = 62,5; promedio 81,25
    assert emision["porcentaje"] == 81 and emision["color"] == "AMARILLO"
    assert {"nombre": "pasarela", "estado": "ABIERTO"} in emision["externas"]


def test_el_acl_caido_deja_las_externas_desconocidas() -> None:
    async def sin_acl():
        raise RuntimeError("acl caido")

    salud = ServicioSalud({n: (lambda: asyncio.sleep(0, True)) for n in MICROS}, sin_acl)
    datos = _calcular(salud)

    assert all(x["estado"] == "DESCONOCIDO" for a in datos["apis"] for x in a["externas"])


def test_disponibilidad_y_latencia_con_metricas() -> None:
    metricas = MetricasFijas(
        {"rating": Metricas(peticiones=1000, disponibilidad=98.0, latencia_media_ms=180)},
        borde=Metricas(peticiones=5000, disponibilidad=99.5, latencia_media_ms=210),
    )
    datos = _calcular(_salud(metricas=metricas))
    rating = next(m for m in datos["micros"] if m["nombre"] == "rating")

    # 70 % × 98 + 30 % × 50 (180 ms frente a 120 ms) = 83,6
    assert rating["porcentaje"] == 84 and rating["color"] == "AMARILLO"
    assert rating["disponibilidad"] == 98.0 and rating["sin_trafico"] is False
    assert datos["disponibilidad"] == 99.5 and datos["latencia_media_ms"] == 210


def test_el_resultado_se_guarda_un_minuto_por_ventana() -> None:
    ahora = [0.0]
    sondeos = []

    async def sonda():
        sondeos.append(1)
        return True

    async def circuitos():
        return CIRCUITOS

    salud = ServicioSalud({n: sonda for n in MICROS}, circuitos, reloj=lambda: ahora[0])
    asyncio.run(salud.calcular("1h"))
    asyncio.run(salud.calcular("1h"))
    asyncio.run(salud.calcular("15m"))
    ahora[0] = 61
    asyncio.run(salud.calcular("1h"))

    assert len(sondeos) == 3 * len(MICROS)


def test_el_socio_no_ve_micros_ni_proveedores() -> None:
    datos = asyncio.run(_salud(caidos={"rating"}).para_socio("1h"))
    texto = json.dumps(datos)

    assert set(datos) == {"porcentaje", "color", "disponibilidad", "latenciaMediaMs", "ventana",
                          "calculadoEn", "apis"}  # fmt: skip
    assert all(n not in texto for n in ("rating", "pasarela", "acl-worker"))


def test_prometheus_arma_disponibilidad_latencia_y_p95() -> None:
    respuestas = iter([200.0, 196.0, 30.0, 200.0, 0.4])

    def manejar(peticion: httpx.Request) -> httpx.Response:
        valor = next(respuestas)
        return httpx.Response(200, json={"data": {"result": [{"value": [0, str(valor)]}]}})

    async def correr():
        async with httpx.AsyncClient(
            base_url="http://prometheus", transport=httpx.MockTransport(manejar)
        ) as cliente:
            return await MetricasPrometheus(cliente).micro("rating", "1h")

    metricas = asyncio.run(correr())

    assert metricas == Metricas(
        peticiones=200.0, disponibilidad=98.0, latencia_media_ms=150, latencia_p95_ms=400
    )


def test_prometheus_caido_es_sin_metricas() -> None:
    def manejar(peticion):
        raise httpx.ConnectError("caido")

    async def correr():
        async with httpx.AsyncClient(
            base_url="http://prometheus", transport=httpx.MockTransport(manejar)
        ) as cliente:
            return await MetricasPrometheus(cliente).micro("rating", "1h")

    assert asyncio.run(correr()) is None


# ---------- HTTP ----------


def test_v1_estado_para_el_socio() -> None:
    e = Entorno()
    e.clientes.acl.circuitos_estado["pasarela"] = "opened"
    with e.cliente() as client:
        bien = client.get("/v1/estado", headers=e.cabeceras())
        ventana_mala = client.get("/v1/estado", params={"ventana": "2h"}, headers=e.cabeceras())

    assert bien.status_code == 200
    assert {a["nombre"] for a in bien.json()["apis"]} == {"COTIZACION", "EMISION"}
    assert ventana_mala.status_code == 422 and ventana_mala.json()["codigo"] == "DATOS_INVALIDOS"


ESTADO_SISTEMA = """
query { estadoSistema(ventana: "15m") {
  porcentaje color ventana apis { nombre color externas { nombre estado } }
  micros { nombre peso responde sinTrafico }
} }
"""


def test_estado_sistema_solo_para_el_backoffice() -> None:
    salud = _salud(caidos={"under"})
    app = create_app(
        config=Configuracion.desde_entorno(), clientes=clientes_falsos(acl=AclFalso()), salud=salud
    )
    with TestClient(app) as client:
        backoffice = client.post(
            "/graphql/web",
            json={"query": ESTADO_SISTEMA},
            headers={"Authorization": f"Bearer {token_de_prueba('BACKOFFICE')}"},
        ).json()
        asesor = client.post(
            "/graphql/web",
            json={"query": ESTADO_SISTEMA},
            headers={"Authorization": f"Bearer {token_de_prueba('ASESOR')}"},
        ).json()

    estado = backoffice["data"]["estadoSistema"]
    assert estado["ventana"] == "15m"
    assert next(m for m in estado["micros"] if m["nombre"] == "under")["responde"] is False
    assert next(a for a in estado["apis"] if a["nombre"] == "EMISION")["color"] == "ROJO"
    assert asesor["data"] is None and asesor["errors"]
