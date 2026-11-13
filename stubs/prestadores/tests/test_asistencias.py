"""stub-prestadores (HU-M06, v0.49, B7): desenlace simulado de la asistencia en sitio."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import PedidoAsistencia, desenlace, distancia_km, minutos_estimados

PEDIDO = {
    "asistencia_id": "0b6f3a52-5d6e-4d8e-9c1a-2f4b7c9d1e00",
    "intento": 1,
    "tipo_asistencia": "MEDICO",
    "prestador_id": "prs-bog-001",
    "prestador_latitud": 4.6859,
    "prestador_longitud": -74.0524,
    "latitud": 4.6760,
    "longitud": -74.0480,
}


@pytest.fixture
def client():
    return TestClient(main.create_app())


def test_health_y_metricas(client):
    assert client.get("/health").json()["estado"] == "ok"
    assert client.get("/metrics").status_code == 200


def test_el_tiempo_usa_las_mismas_reglas_de_la_app():
    km = distancia_km(4.6760, -74.0480, 4.6859, -74.0524)
    assert round(km, 2) == 1.2
    assert minutos_estimados(km) == 6  # ceil(1.2 km a 20 km/h = 3,6 min) + 2


def test_primero_falla_y_el_reintento_se_confirma(client):
    r = client.post("/v1/asistencias", json=PEDIDO)
    assert r.status_code == 200
    assert r.json() == {"estado": "NO_CONFIRMADA", "motivo": "PRESTADOR_SIN_RESPUESTA"}
    r = client.post("/v1/asistencias", json={**PEDIDO, "intento": 2})
    assert r.json() == {"estado": "CONFIRMADA", "minutos_estimados": 6, "motivo": None}


def test_confirma_siempre_y_sin_coordenadas_no_hay_tiempo():
    pedido = PedidoAsistencia(**{**PEDIDO, "latitud": None, "longitud": None})
    assert desenlace(pedido, "confirma", None) == {
        "estado": "CONFIRMADA",
        "minutos_estimados": None,
        "motivo": None,
    }


def test_minutos_fijos_para_probar_la_llegada():
    pedido = PedidoAsistencia(**PEDIDO)
    assert desenlace(pedido, "confirma", 1)["minutos_estimados"] == 1


def test_simulacion_desconocida_no_arranca(monkeypatch):
    monkeypatch.setenv("PRESTADORES_SIMULACION", "siempre")
    with pytest.raises(ValueError):
        main.create_app()


def test_modo_error_y_override_por_peticion(client):
    assert client.post("/control/modo", json={"modo": "error"}).json()["modo"] == "error"
    assert client.post("/v1/asistencias", json=PEDIDO).status_code == 503
    assert client.get("/v1/estado").status_code == 503
    assert client.post("/v1/asistencias", json=PEDIDO, params={"modo": "sano"}).status_code == 200
    assert client.get("/v1/estado", params={"modo": "rayo"}).status_code == 400


def test_valida_el_pedido(client):
    assert client.post("/v1/asistencias", json={**PEDIDO, "intento": 0}).status_code == 422
