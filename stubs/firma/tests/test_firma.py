"""El stub de firma: idempotente por referencia, modos de falla y las dos APIs (EC031)."""

from fastapi.testclient import TestClient

from app.main import create_app

FIRMA = {
    "referencia_comercio": "propuesta-1",
    "documento_hash": "a" * 64,
    "version_documento": "v1",
    "firmante_id": "usuario-1",
}
FIRMA_ALTERNA = {
    "signature_request_id": "propuesta-1",
    "document_digest": "a" * 64,
    "document_version": "v1",
    "signer": "usuario-1",
}


def test_firma_y_devuelve_la_evidencia() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/firmas", json=FIRMA)

    assert r.status_code == 200
    assert r.json()["estado"] == "firmada"
    assert r.json()["referencia"].startswith("fe-")
    assert r.json()["firmada_en"]
    assert "documento_hash" not in r.json()


def test_repetir_la_referencia_devuelve_la_misma_evidencia_sin_firmar_de_nuevo() -> None:
    with TestClient(create_app()) as client:
        primera = client.post("/v1/firmas", json=FIRMA).json()
        segunda = client.post("/v1/firmas", json=FIRMA).json()

    assert primera == segunda


def test_las_dos_apis_comparten_la_memoria_de_firmas() -> None:
    with TestClient(create_app()) as client:
        estandar = client.post("/v1/firmas", json=FIRMA).json()
        alterna = client.post("/alterna/v1/signatures", json=FIRMA_ALTERNA).json()

    assert alterna["status"] == "SIGNED"
    assert alterna["signature_id"] == estandar["referencia"]


def test_modo_error_responde_503_y_no_registra_la_firma() -> None:
    with TestClient(create_app()) as client:
        caida = client.post("/v1/firmas?modo=error", json=FIRMA)
        consulta = client.get("/v1/firmas/propuesta-1")

    assert caida.status_code == 503
    assert consulta.status_code == 404


def test_modo_lento_registra_la_firma_antes_de_la_espera() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/firmas?modo=lento", json=FIRMA)
        consulta = client.get("/v1/firmas/propuesta-1")

    assert r.status_code == 200
    assert consulta.status_code == 200  # igual quedó hecha aunque el ACL corte a los 700 ms


def test_el_estado_obedece_el_modo_y_no_firma() -> None:
    with TestClient(create_app()) as client:
        sano = client.get("/v1/estado")
        error = client.get("/v1/estado?modo=error")
        consulta = client.get("/v1/firmas/propuesta-1")

    assert sano.status_code == 200
    assert error.status_code == 503
    assert consulta.status_code == 404  # sondear nunca crea una firma


def test_un_modo_invalido_responde_400() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/control/modo", json={"modo": "rechaza"}).status_code == 422
        assert client.post("/v1/firmas?modo=rechaza", json=FIRMA).status_code == 400
