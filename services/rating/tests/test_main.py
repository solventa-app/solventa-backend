"""Pruebas de los endpoints `GET /perfiles/{cliente_id}` y `POST /ofertas`
con dobles de prueba (sin MongoDB ni Redis reales — la verificación en vivo
se hace con `docker compose`, ver README.md)."""

from fakes import LectorPerfilesFalso, ServicioOfertaFalso
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


OFERTA_EJEMPLO = {
    "id": "oferta-1",
    "tipo": "COMPLETA",
    "prima": {"valor": "94680.00", "moneda": "COP"},
    "rangoPrima": None,
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "desgloseScore": [{"nombre": "historial-crediticio", "peso": 0.30}],
    "fuentes": [],
}


def test_generar_oferta_devuelve_la_oferta_calculada() -> None:
    servicio = ServicioOfertaFalso(oferta=OFERTA_EJEMPLO, score_de_cache=False)

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "1700000001.1",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 200
    assert r.json() == OFERTA_EJEMPLO
    assert servicio.llamadas == [("cliente-1", "1700000001.1", "200000000")]


def test_generar_oferta_sin_perfil_responde_404_no_5xx() -> None:
    servicio = ServicioOfertaFalso(oferta=None)

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "sin-perfil",
                "operation_time": "1.0",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 404


def test_generar_oferta_con_operation_time_invalido_responde_400_no_5xx() -> None:
    servicio = ServicioOfertaFalso(error=ValueError("formato invalido"))

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "no-es-un-timestamp",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 400


def test_generar_oferta_con_cobertura_invalida_responde_400_no_5xx() -> None:
    from decimal import InvalidOperation

    servicio = ServicioOfertaFalso(error=InvalidOperation("cobertura invalida"))

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "1700000001.1",
                "cobertura": "no-es-un-numero",
            },
        )

    assert r.status_code == 400


def test_generar_oferta_sin_campos_requeridos_responde_422() -> None:
    servicio = ServicioOfertaFalso()

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post("/ofertas", json={"cliente_id": "cliente-1"})

    assert r.status_code == 422
