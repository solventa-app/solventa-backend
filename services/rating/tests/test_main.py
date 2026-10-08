"""Pruebas del endpoint `GET /perfiles/{cliente_id}` con dobles de prueba
(sin MongoDB real — la verificación de lectura causal real contra el
replica set se hace en vivo con `docker compose`, ver README.md)."""

from fakes import LectorPerfilesFalso
from fastapi.testclient import TestClient

from app.main import create_app


def test_obtener_perfil_devuelve_el_perfil_leido() -> None:
    lector = LectorPerfilesFalso(
        perfil={
            "cliente_id": "cliente-1",
            "perfil_version": 1,
            "fuentes": [],
            "leido_de_secundaria": True,
        }
    )

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/cliente-1", params={"operation_time": "1700000001.1"})

    assert r.status_code == 200
    assert r.json()["perfil_version"] == 1
    assert r.json()["leido_de_secundaria"] is True
    assert lector.llamadas == [("cliente-1", "1700000001.1")]


def test_obtener_perfil_inexistente_responde_404_no_5xx() -> None:
    lector = LectorPerfilesFalso(perfil=None)

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/sin-perfil", params={"operation_time": "1.0"})

    assert r.status_code == 404


def test_operation_time_invalido_responde_400_no_5xx() -> None:
    lector = LectorPerfilesFalso()

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/cliente-1", params={"operation_time": "no-es-un-timestamp"})

    assert r.status_code == 400


def test_sin_operation_time_responde_422() -> None:
    lector = LectorPerfilesFalso()

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/cliente-1")

    assert r.status_code == 422
