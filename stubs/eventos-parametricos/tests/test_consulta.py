"""Pruebas del stub de las fuentes paramétricas (clima y vuelos)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _fijar_modo(modo: str) -> None:
    client.post("/control/modo", json={"modo": modo})


def _fijar_umbral(modo: str) -> None:
    client.post("/control/umbral", json={"modo": modo})


@pytest.fixture(autouse=True)
def _restablecer() -> None:
    _fijar_modo("sano")
    _fijar_umbral("automatico")
    yield
    _fijar_modo("sano")
    _fijar_umbral("automatico")


def test_health_y_metricas() -> None:
    assert client.get("/health").json()["estado"] == "ok"
    assert client.get("/metrics").status_code == 200


def test_forecast_entrega_una_serie_horaria_de_24_valores() -> None:
    r = client.get(
        "/v1/forecast",
        params={"latitude": 4.61, "longitude": -74.08, "hourly": "precipitation"},
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert len(cuerpo["hourly"]["precipitation"]) == 24
    assert all(v >= 0 for v in cuerpo["hourly"]["precipitation"])


def test_forecast_es_determinista_para_la_misma_celda() -> None:
    params = {"latitude": 4.61, "longitude": -74.08, "hourly": "wind_speed_10m"}
    uno = client.get("/v1/forecast", params=params).json()
    dos = client.get("/v1/forecast", params=params).json()
    assert uno == dos


def test_forecast_con_variable_desconocida_es_422() -> None:
    r = client.get(
        "/v1/forecast", params={"latitude": 4.61, "longitude": -74.08, "hourly": "no-existe"}
    )
    assert r.status_code == 422


def test_forecast_respeta_el_umbral_forzado() -> None:
    params = {"latitude": 10.0, "longitude": -75.5, "hourly": "precipitation"}
    _fijar_umbral("sobre_umbral")
    alto = max(client.get("/v1/forecast", params=params).json()["hourly"]["precipitation"])
    _fijar_umbral("bajo_umbral")
    bajo = max(client.get("/v1/forecast", params=params).json()["hourly"]["precipitation"])
    assert alto > bajo


def test_flights_vuelo_de_demo_sobre_el_umbral() -> None:
    r = client.get("/v1/flights", params={"flight_iata": "AV9340", "flight_date": "2026-11-11"})
    assert r.status_code == 200
    vuelo = r.json()["data"][0]
    assert vuelo["departure"]["delay"] == 145
    assert vuelo["flight_status"] == "active"


def test_flights_vuelo_cancelado() -> None:
    r = client.get("/v1/flights", params={"flight_iata": "la5678", "flight_date": "2026-11-11"})
    vuelo = r.json()["data"][0]
    assert vuelo["flight_status"] == "cancelled"


def test_flights_vuelo_inexistente_da_data_vacio() -> None:
    r = client.get("/v1/flights", params={"flight_iata": "NOOP00", "flight_date": "2026-11-11"})
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_flights_vuelo_sintetico_respeta_el_umbral_forzado() -> None:
    params = {"flight_iata": "ZZ0001", "flight_date": "2026-11-11"}
    _fijar_umbral("sobre_umbral")
    alto = client.get("/v1/flights", params=params).json()["data"][0]["departure"]["delay"]
    _fijar_umbral("bajo_umbral")
    bajo = client.get("/v1/flights", params=params).json()["data"][0]["departure"]["delay"]
    assert alto > bajo


def test_modos_de_falla_para_el_circuit_breaker() -> None:
    _fijar_modo("error")
    r = client.get(
        "/v1/forecast", params={"latitude": 4.61, "longitude": -74.08, "hourly": "precipitation"}
    )
    assert r.status_code == 503
    r2 = client.get("/v1/flights", params={"flight_iata": "AV9340", "flight_date": "2026-11-11"})
    assert r2.status_code == 503


def test_modo_por_header_tiene_prioridad_sobre_el_global() -> None:
    _fijar_modo("sano")
    r = client.get(
        "/v1/forecast",
        params={"latitude": 4.61, "longitude": -74.08, "hourly": "precipitation"},
        headers={"x-modo-simulado": "error"},
    )
    assert r.status_code == 503
