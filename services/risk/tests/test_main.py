"""Pruebas del endpoint `POST /perfiles` con dobles de prueba (sin MongoDB
real — la verificación con el replica set real se hace en vivo con
`docker compose`, ver README.md)."""

from fakes import BusEventosFalso, RepositorioPerfilesFalso
from fastapi.testclient import TestClient

from app.application.bus_eventos import BusEventosLog
from app.application.servicio_perfiles import ServicioPerfiles
from app.main import create_app

CUERPO = {
    "cliente_id": "cliente-1",
    "fuentes": [
        {
            "fuente": "cuentas-bancarias",
            "datos": {"saldo": 100},
            "capturado_en": "2026-10-07T12:00:00+00:00",
            "de_cache": False,
            "degradado": False,
        },
        {
            "fuente": "afiliacion-pila",
            "datos": {},
            "capturado_en": "2026-10-07T12:00:00+00:00",
            "de_cache": False,
            "degradado": True,
        },
    ],
}


def test_crear_perfil_devuelve_version_y_operation_time() -> None:
    servicio = ServicioPerfiles(RepositorioPerfilesFalso(), BusEventosLog())

    with TestClient(create_app(servicio_perfiles=servicio)) as client:
        r = client.post("/perfiles", json=CUERPO)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["cliente_id"] == "cliente-1"
    assert cuerpo["perfil_version"] == 1
    assert cuerpo["operation_time"] == "1700000001.1"


def test_crear_perfil_emite_perfil_actualizado() -> None:
    bus = BusEventosFalso()
    servicio = ServicioPerfiles(RepositorioPerfilesFalso(), bus)

    with TestClient(create_app(servicio_perfiles=servicio)) as client:
        client.post("/perfiles", json=CUERPO)

    assert len(bus.eventos) == 1
    assert bus.eventos[0]["datos"]["clienteId"] == "cliente-1"


def test_crear_perfil_dos_veces_incrementa_version() -> None:
    servicio = ServicioPerfiles(RepositorioPerfilesFalso(), BusEventosLog())

    with TestClient(create_app(servicio_perfiles=servicio)) as client:
        client.post("/perfiles", json=CUERPO)
        r = client.post("/perfiles", json=CUERPO)

    assert r.json()["perfil_version"] == 2
