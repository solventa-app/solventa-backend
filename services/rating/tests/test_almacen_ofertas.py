"""La oferta generada se guarda para poder aceptarla (HU-W03, decisión W03-1)."""

import json
from datetime import UTC, datetime

from fakes import AlmacenClaveValorFalso, ServicioOfertaFalso
from fastapi.testclient import TestClient

from app.application.almacen_ofertas import AlmacenOfertas
from app.main import create_app

OFERTA = {
    "id": "oferta-1",
    "tipo": "COMPLETA",
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "200000000", "moneda": "COP"},
}
SOLICITUD = {
    "cliente_id": "cliente-opaco",
    "operation_time": "1700000001.1",
    "cobertura": "200000000",
    "plazo_meses": 60,
    "consentimiento_id": "consentimiento-1",
}


def test_guarda_y_devuelve_la_oferta_con_cliente_y_consentimiento() -> None:
    almacen = AlmacenOfertas(AlmacenClaveValorFalso(), 3600)

    almacen.guardar(OFERTA, "cliente-opaco", "consentimiento-1", 60)
    leida = almacen.obtener("oferta-1")

    assert leida is not None
    assert leida["prima"] == OFERTA["prima"]
    assert leida["clienteId"] == "cliente-opaco"
    assert leida["consentimientoId"] == "consentimiento-1"
    assert leida["plazoMeses"] == 60
    assert datetime.fromisoformat(leida["vigenteHasta"]) > datetime.now(UTC)


def test_una_oferta_desconocida_no_existe() -> None:
    assert AlmacenOfertas(AlmacenClaveValorFalso(), 3600).obtener("no-existe") is None


def test_el_ttl_es_la_vigencia() -> None:
    llamadas: list[int | None] = []

    class Registra(AlmacenClaveValorFalso):
        def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
            llamadas.append(ex)
            super().set(nombre, valor, ex)

    AlmacenOfertas(Registra(), 1800).guardar(OFERTA, "c", None, None)

    assert llamadas == [1800]


def test_un_fallo_de_redis_no_rompe_ni_al_guardar_ni_al_leer() -> None:
    class RedisCaido:
        def get(self, nombre: str) -> str:
            raise ConnectionError("redis caído")

        def set(self, nombre: str, valor: str, ex: int | None = None) -> None:
            raise ConnectionError("redis caído")

    almacen = AlmacenOfertas(RedisCaido(), 3600)

    almacen.guardar(OFERTA, "c", None, None)  # no lanza
    assert almacen.obtener("oferta-1") is None


def test_sin_redis_no_guarda_ni_lee() -> None:
    almacen = AlmacenOfertas(None, 3600)

    almacen.guardar(OFERTA, "c", None, None)

    assert almacen.obtener("oferta-1") is None


def test_post_ofertas_guarda_la_oferta_y_get_la_devuelve() -> None:
    valores = AlmacenClaveValorFalso()
    app = create_app(
        servicio_oferta=ServicioOfertaFalso(oferta=OFERTA),
        almacen_ofertas=AlmacenOfertas(valores, 3600),
    )

    with TestClient(app) as client:
        generada = client.post("/ofertas", json=SOLICITUD)
        leida = client.get("/ofertas/oferta-1")

    assert generada.status_code == 200
    assert "clienteId" not in generada.json()  # la respuesta al BFF no cambia
    assert leida.status_code == 200
    assert leida.json()["clienteId"] == "cliente-opaco"
    assert leida.json()["consentimientoId"] == "consentimiento-1"
    assert json.loads(valores.valores["oferta-aceptable:oferta-1"])["plazoMeses"] == 60


def test_get_de_una_oferta_vencida_o_desconocida_responde_404() -> None:
    app = create_app(
        servicio_oferta=ServicioOfertaFalso(oferta=OFERTA),
        almacen_ofertas=AlmacenOfertas(AlmacenClaveValorFalso(), 3600),
    )

    with TestClient(app) as client:
        respuesta = client.get("/ofertas/vencida")

    assert respuesta.status_code == 404
