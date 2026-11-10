import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def consultar(fuente: str, placa: str, **params):
    return client.post(f"/v1/fuentes/{fuente}/consulta", json={"placa": placa}, params=params)


def test_health_y_metricas():
    assert client.get("/health").json()["estado"] == "ok"
    assert client.get("/metrics").status_code == 200


def test_runt_entrega_los_datos_de_una_placa_de_demo():
    r = consultar("runt-vehiculo", "abc-123")
    assert r.status_code == 200
    datos = r.json()["datos"]
    assert datos["placa"] == "ABC123"
    assert (datos["marca"], datos["modelo"], datos["clase"]) == ("MAZDA", 2024, "AUTOMOVIL")
    assert datos["estado"] == "ACTIVO"
    assert datos["odometro_km"] > 0


def test_fasecolda_y_siniestralidad_de_la_misma_placa():
    valor = consultar("fasecolda-valor", "GHI789").json()["datos"]
    assert valor["valor_comercial"] == "285000000"
    assert valor["indice_hurto"] == "MUY_ALTO"
    sini = consultar("siniestralidad-placa", "JKL012").json()["datos"]
    assert sini["siniestros_3_anios"] == 3
    assert len(sini["detalle"]) == 3


@pytest.mark.parametrize(("placa", "estado"), [("MNO345", "CON_LIMITACION"), ("STU901", "HURTADO")])
def test_estados_que_impiden_asegurar(placa, estado):
    assert consultar("runt-vehiculo", placa).json()["datos"]["estado"] == estado


def test_placa_inexistente_y_placa_invalida():
    assert consultar("runt-vehiculo", "NOX000").status_code == 404
    assert consultar("runt-vehiculo", "12ABC").status_code == 422


def test_la_referencia_sin_guia_falla_solo_en_fasecolda():
    assert consultar("runt-vehiculo", "YZA456").status_code == 200
    assert consultar("fasecolda-valor", "YZA456").status_code == 503


def test_una_placa_cualquiera_da_siempre_el_mismo_vehiculo():
    uno = consultar("runt-vehiculo", "QWE987").json()["datos"]
    dos = consultar("runt-vehiculo", "QWE987").json()["datos"]
    assert uno == dos
    assert 2015 <= uno["modelo"] <= 2026
    assert consultar("runt-vehiculo", "QWE98K").json()["datos"]["clase"] == "MOTOCICLETA"


def test_modos_de_falla_para_el_circuit_breaker():
    assert consultar("runt-vehiculo", "ABC123", modo="error").status_code == 503
    client.post("/control/modo", json={"modo": "error"})
    try:
        assert consultar("fasecolda-valor", "ABC123").status_code == 503
    finally:
        client.post("/control/modo", json={"modo": "sano"})
    assert consultar("fasecolda-valor", "ABC123").status_code == 200


def test_fuente_desconocida_y_placas_de_demo():
    assert consultar("simit", "ABC123").status_code == 404
    assert "ABC123" in client.get("/control/placas").json()
