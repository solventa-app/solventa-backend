import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _fijar_modo(modo: str) -> None:
    r = client.post("/control/modo", json={"modo": modo})
    assert r.status_code == 200
    assert r.json()["modo"] == modo


def teardown_function() -> None:
    _fijar_modo("sano")


def test_rechaza_fuente_desconocida() -> None:
    r = client.post("/v1/fuentes/no-existe/consulta", json={"cliente_id": "c-1"})
    assert r.status_code == 404


def test_modo_sano_responde_cada_una_de_las_3_fuentes() -> None:
    _fijar_modo("sano")
    for fuente in ("afiliacion-pila", "camara-comercio", "antecedentes-judiciales"):
        r = client.post(f"/v1/fuentes/{fuente}/consulta", json={"cliente_id": "c-1"})
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["fuente"] == fuente
        assert "datos" in cuerpo and "capturado_en" in cuerpo


def test_modo_error_responde_5xx() -> None:
    _fijar_modo("error")
    r = client.post("/v1/fuentes/camara-comercio/consulta", json={"cliente_id": "c-1"})
    assert r.status_code == 503


def test_override_por_header_no_persiste() -> None:
    _fijar_modo("sano")
    r = client.post(
        "/v1/fuentes/camara-comercio/consulta",
        json={"cliente_id": "c-1"},
        headers={"X-Modo-Simulado": "error"},
    )
    assert r.status_code == 503
    assert client.get("/control/modo").json()["modo"] == "sano"


def test_modo_lento_supera_700ms() -> None:
    _fijar_modo("lento")
    inicio = time.monotonic()
    r = client.post("/v1/fuentes/afiliacion-pila/consulta", json={"cliente_id": "c-1"})
    duracion_ms = (time.monotonic() - inicio) * 1000
    assert r.status_code == 200
    assert duracion_ms > 700
