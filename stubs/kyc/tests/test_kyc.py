"""El stub de KYC: idempotente por referencia, determinista y con los modos de falla."""

from fastapi.testclient import TestClient

from app.main import create_app

SOLICITUD = {"referencia_comercio": "propuesta-1", "cliente_id": "cliente-opaco"}


def test_aprueba_y_devuelve_la_referencia() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/verificaciones", json=SOLICITUD)

    assert r.status_code == 200
    assert r.json()["estado"] == "aprobada"
    assert r.json()["referencia"].startswith("kyc-")
    assert r.json()["verificada_en"]


def test_repetir_la_referencia_devuelve_el_mismo_resultado() -> None:
    with TestClient(create_app()) as client:
        primera = client.post("/v1/verificaciones", json=SOLICITUD).json()
        segunda = client.post("/v1/verificaciones", json=SOLICITUD).json()

    assert primera == segunda


def test_modo_rechaza_responde_200_con_el_rechazo() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/verificaciones?modo=rechaza", json=SOLICITUD)

    assert r.status_code == 200
    assert r.json()["estado"] == "rechazada"
    assert r.json()["motivo"] == "identidad_no_verificada"
    assert r.json()["referencia"] is None


def test_modo_error_responde_503_y_no_registra_la_verificacion() -> None:
    with TestClient(create_app()) as client:
        caida = client.post("/v1/verificaciones?modo=error", json=SOLICITUD)
        consulta = client.get("/v1/verificaciones/propuesta-1")

    assert caida.status_code == 503
    assert consulta.status_code == 404


def test_modo_lento_registra_la_verificacion_antes_de_la_espera() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/verificaciones?modo=lento", json=SOLICITUD)
        consulta = client.get("/v1/verificaciones/propuesta-1")

    assert r.status_code == 200
    assert consulta.status_code == 200  # igual quedó hecha aunque el ACL corte a los 700 ms


def test_el_estado_obedece_el_modo_y_no_verifica() -> None:
    with TestClient(create_app()) as client:
        sano = client.get("/v1/estado")
        error = client.get("/v1/estado?modo=error")
        consulta = client.get("/v1/verificaciones/propuesta-1")

    assert sano.status_code == 200
    assert error.status_code == 503
    assert consulta.status_code == 404  # sondear nunca crea una verificación


def test_el_control_de_modo_acepta_rechaza_y_rechaza_los_demas() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/control/modo", json={"modo": "rechaza"}).status_code == 200
        assert client.post("/control/modo", json={"modo": "revision"}).status_code == 422
        assert client.post("/v1/verificaciones?modo=revision", json=SOLICITUD).status_code == 400
