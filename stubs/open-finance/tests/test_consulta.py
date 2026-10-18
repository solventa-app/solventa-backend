import time

from fastapi.testclient import TestClient

from app.main import DETALLE_SIN_CONSENTIMIENTO, app

client = TestClient(app)

CABECERAS_CONSENTIMIENTO = {
    "X-Consentimiento-Id": "consent-1",
    "X-Finalidad": "perfilamiento",
}


def _fijar_modo(modo: str) -> None:
    r = client.post("/control/modo", json={"modo": modo})
    assert r.status_code == 200
    assert r.json()["modo"] == modo


def teardown_function() -> None:
    _fijar_modo("sano")


def test_rechaza_fuente_desconocida() -> None:
    r = client.post(
        "/v1/fuentes/no-existe/consulta",
        json={"cliente_id": "c-1"},
        headers=CABECERAS_CONSENTIMIENTO,
    )
    assert r.status_code == 404


def test_rechaza_403_sin_consentimiento_ni_finalidad() -> None:
    r = client.post("/v1/fuentes/cuentas-bancarias/consulta", json={"cliente_id": "c-1"})
    assert r.status_code == 403
    assert r.json() == {"detail": DETALLE_SIN_CONSENTIMIENTO}


def test_rechaza_403_con_consentimiento_o_finalidad_vacios() -> None:
    r_sin_finalidad = client.post(
        "/v1/fuentes/cuentas-bancarias/consulta",
        json={"cliente_id": "c-1"},
        headers={"X-Consentimiento-Id": "consent-1", "X-Finalidad": "   "},
    )
    assert r_sin_finalidad.status_code == 403
    assert r_sin_finalidad.json() == {"detail": DETALLE_SIN_CONSENTIMIENTO}

    r_ruta_sin_consentimiento = client.get(
        "/open-finance/cuentas-bancarias/c-1",
        params={"consentimiento_id": "", "finalidad": "perfilamiento"},
    )
    assert r_ruta_sin_consentimiento.status_code == 403
    assert r_ruta_sin_consentimiento.json() == {"detail": DETALLE_SIN_CONSENTIMIENTO}


def test_acepta_consentimiento_y_finalidad_por_query_param_en_open_finance_ruta() -> None:
    _fijar_modo("sano")
    r = client.get(
        "/open-finance/cuentas-bancarias/c-1",
        params={"consentimiento_id": "consent-1", "finalidad": "perfilamiento"},
    )
    assert r.status_code == 200
    assert r.json()["fuente"] == "cuentas-bancarias"


def test_modo_sano_responde_datos() -> None:
    _fijar_modo("sano")
    r = client.post(
        "/v1/fuentes/cuentas-bancarias/consulta",
        json={"cliente_id": "c-1"},
        headers=CABECERAS_CONSENTIMIENTO,
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["fuente"] == "cuentas-bancarias"
    assert "datos" in cuerpo and "capturado_en" in cuerpo


def test_modo_error_responde_5xx() -> None:
    _fijar_modo("error")
    r = client.post(
        "/v1/fuentes/historial-crediticio/consulta",
        json={"cliente_id": "c-1"},
        headers=CABECERAS_CONSENTIMIENTO,
    )
    assert r.status_code == 503


def test_override_por_query_param_no_persiste() -> None:
    _fijar_modo("sano")
    r = client.post(
        "/v1/fuentes/cuentas-bancarias/consulta?modo=error",
        json={"cliente_id": "c-1"},
        headers=CABECERAS_CONSENTIMIENTO,
    )
    assert r.status_code == 503
    # el modo global sigue siendo "sano": el override de un query param no persiste
    assert client.get("/control/modo").json()["modo"] == "sano"


def test_override_por_header() -> None:
    _fijar_modo("sano")
    r = client.post(
        "/v1/fuentes/cuentas-bancarias/consulta",
        json={"cliente_id": "c-1"},
        headers={**CABECERAS_CONSENTIMIENTO, "X-Modo-Simulado": "error"},
    )
    assert r.status_code == 503


def test_modo_lento_supera_700ms() -> None:
    _fijar_modo("lento")
    inicio = time.monotonic()
    r = client.post(
        "/v1/fuentes/cuentas-bancarias/consulta",
        json={"cliente_id": "c-1"},
        headers=CABECERAS_CONSENTIMIENTO,
    )
    duracion_ms = (time.monotonic() - inicio) * 1000
    assert r.status_code == 200
    assert duracion_ms > 700

