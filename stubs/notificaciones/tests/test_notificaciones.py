"""El stub de notificaciones: idempotente por referencia, registra lo enviado y falla por modos."""

from fastapi.testclient import TestClient

from app.main import create_app

AVISO = {
    "referencia_comercio": "poliza-emitida-pz-1",
    "plantilla": "poliza-emitida",
    "destinatario_id": "usuario-1",
    "datos": {"numero_poliza": "SOL-2026-000001", "enlace_documento": "/polizas/pz-1/documento"},
}


def test_envia_y_devuelve_la_referencia() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/notificaciones", json=AVISO)

    assert r.status_code == 200
    assert r.json()["estado"] == "enviada"
    assert r.json()["referencia"].startswith("nt-")


def test_repetir_la_referencia_no_envia_otro_aviso() -> None:
    with TestClient(create_app()) as client:
        primero = client.post("/v1/notificaciones", json=AVISO).json()
        segundo = client.post("/v1/notificaciones", json=AVISO).json()
        enviados = client.get("/control/enviados").json()

    assert primero == segundo
    assert len(enviados) == 1  # se avisó una sola vez


def test_lo_enviado_lleva_la_plantilla_el_id_opaco_y_los_datos() -> None:
    with TestClient(create_app()) as client:
        client.post("/v1/notificaciones", json=AVISO)
        enviado = client.get("/control/enviados").json()[0]

    assert enviado["plantilla"] == "poliza-emitida"
    assert enviado["destinatario_id"] == "usuario-1"
    assert enviado["datos"]["numero_poliza"] == "SOL-2026-000001"


def test_modo_error_responde_503_y_no_registra_el_aviso() -> None:
    with TestClient(create_app()) as client:
        caida = client.post("/v1/notificaciones?modo=error", json=AVISO)
        enviados = client.get("/control/enviados").json()

    assert caida.status_code == 503
    assert enviados == []


def test_modo_lento_registra_el_aviso_antes_de_la_espera() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/notificaciones?modo=lento", json=AVISO)
        enviados = client.get("/control/enviados").json()

    assert r.status_code == 200
    assert len(enviados) == 1  # igual quedó enviado aunque el ACL corte a los 700 ms


def test_el_estado_obedece_el_modo_y_no_envia() -> None:
    with TestClient(create_app()) as client:
        sano = client.get("/v1/estado")
        error = client.get("/v1/estado?modo=error")
        enviados = client.get("/control/enviados").json()

    assert sano.status_code == 200
    assert error.status_code == 503
    assert enviados == []  # sondear nunca manda un aviso


def test_un_modo_invalido_se_rechaza() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/control/modo", json={"modo": "rechaza"}).status_code == 422
        assert client.post("/v1/notificaciones?modo=rechaza", json=AVISO).status_code == 400
