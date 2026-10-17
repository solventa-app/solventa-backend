import time
import uuid

from fastapi.testclient import TestClient

from app.main import app, create_app

client = TestClient(app)

ORDEN = {
    "referencia_comercio": "idem-1",
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


def _orden(clave: str | None = None) -> dict:
    return {**ORDEN, "referencia_comercio": clave or f"idem-{uuid.uuid4()}"}


def test_misma_clave_devuelve_el_cobro_original_sin_cobrar_de_nuevo() -> None:
    cliente = TestClient(create_app())
    orden = _orden()

    primera = cliente.post("/v1/cobros", json=orden).json()
    segunda = cliente.post("/v1/cobros", json=orden).json()

    assert primera["estado"] == "cobrado"
    assert segunda["referencia"] == primera["referencia"]


def test_lento_registra_el_cobro_aunque_responda_tarde() -> None:
    cliente = TestClient(create_app())
    orden = _orden()

    cliente.post("/v1/cobros", json=orden, headers={"X-Modo-Simulado": "lento"})
    consulta = cliente.get(f"/v1/cobros/{orden['referencia_comercio']}")

    assert consulta.status_code == 200
    assert consulta.json()["estado"] == "cobrado"


def test_error_no_registra_el_cobro() -> None:
    cliente = TestClient(create_app())
    orden = _orden()

    assert cliente.post("/v1/cobros", json=orden, params={"modo": "error"}).status_code == 503
    assert cliente.get(f"/v1/cobros/{orden['referencia_comercio']}").status_code == 404


def test_rechaza_responde_200_con_estado_rechazado() -> None:
    cliente = TestClient(create_app())

    r = cliente.post("/v1/cobros", json=_orden(), headers={"X-Modo-Simulado": "rechaza"})

    assert r.status_code == 200  # un rechazo es un resultado de negocio, no una falla
    assert r.json()["estado"] == "rechazado"
    assert r.json()["motivo"] == "fondos_insuficientes"
    assert r.json()["referencia"] is None


def test_un_rechazo_tambien_se_recuerda() -> None:
    cliente = TestClient(create_app())
    orden = _orden()

    cliente.post("/v1/cobros", json=orden, headers={"X-Modo-Simulado": "rechaza"})
    repetido = cliente.post("/v1/cobros", json=orden)  # ya en modo sano

    assert repetido.json()["estado"] == "rechazado"


def test_consultar_un_cobro_inexistente_responde_404() -> None:
    cliente = TestClient(create_app())

    assert cliente.get("/v1/cobros/no-existe").status_code == 404


def test_consultar_obedece_el_modo() -> None:
    cliente = TestClient(create_app())

    assert cliente.get("/v1/cobros/cualquiera", params={"modo": "error"}).status_code == 503
