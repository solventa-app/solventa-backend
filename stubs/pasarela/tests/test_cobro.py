import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ORDEN = {
    "clave_idempotencia": "idem-1",
    "poliza_id": "pol-1",
    "monto": "150000.00",
    "moneda": "COP",
    "token_medio_pago": "tok_abc123",
}


def _fijar_modo(modo: str) -> None:
    r = client.post("/control/modo", json={"modo": modo})
    assert r.status_code == 200
    assert r.json()["modo"] == modo


def teardown_function() -> None:
    _fijar_modo("sano")


def test_modo_sano_cobra() -> None:
    _fijar_modo("sano")
    r = client.post("/v1/cobros", json=ORDEN)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "cobrado"
    assert cuerpo["referencia"].startswith("pg-")


def test_modo_error_responde_5xx() -> None:
    _fijar_modo("error")
    r = client.post("/v1/cobros", json=ORDEN)
    assert r.status_code == 503


def test_modo_lento_supera_700ms() -> None:
    _fijar_modo("lento")
    inicio = time.monotonic()
    r = client.post("/v1/cobros", json=ORDEN)
    duracion_ms = (time.monotonic() - inicio) * 1000
    assert r.status_code == 200
    assert duracion_ms > 700


def test_estado_proveedor_obedece_el_modo() -> None:
    _fijar_modo("error")
    r = client.get("/v1/estado")
    assert r.status_code == 503


def test_estado_proveedor_sano() -> None:
    _fijar_modo("sano")
    r = client.get("/v1/estado")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_health_no_obedece_el_modo() -> None:
    _fijar_modo("error")
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok"
