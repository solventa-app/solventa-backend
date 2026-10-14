"""Pruebas de los endpoints de consentimiento sobre el mock en memoria."""

from fastapi.testclient import TestClient

from app.main import create_app

SOLICITUD = {"finalidad": "cotizacion-vida-hipotecario", "version_texto": "v1"}


def _cliente() -> TestClient:
    return TestClient(create_app())


def test_registrar_devuelve_consentimiento_vigente() -> None:
    r = _cliente().post("/consentimientos", json=SOLICITUD)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["finalidad"] == "cotizacion-vida-hipotecario"
    assert cuerpo["version_texto"] == "v1"
    assert cuerpo["vigente"] is True
    assert cuerpo["revocado_en"] is None
    assert cuerpo["otorgado_en"]


def test_obtener_devuelve_el_registrado() -> None:
    client = _cliente()
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    r = client.get(f"/consentimientos/{creado['id']}")

    assert r.status_code == 200
    assert r.json() == creado


def test_obtener_inexistente_responde_404() -> None:
    assert _cliente().get("/consentimientos/no-existe").status_code == 404


def test_revocar_marca_revocado_sin_borrar() -> None:
    client = _cliente()
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    r = client.post(f"/consentimientos/{creado['id']}/revocar")

    assert r.status_code == 200
    assert r.json()["vigente"] is False
    assert r.json()["revocado_en"] is not None
    # Append-only: sigue existiendo, ahora revocado.
    assert client.get(f"/consentimientos/{creado['id']}").json()["vigente"] is False


def test_revocar_dos_veces_conserva_la_primera_fecha() -> None:
    client = _cliente()
    creado = client.post("/consentimientos", json=SOLICITUD).json()

    primera = client.post(f"/consentimientos/{creado['id']}/revocar").json()
    segunda = client.post(f"/consentimientos/{creado['id']}/revocar").json()

    assert segunda["revocado_en"] == primera["revocado_en"]


def test_revocar_inexistente_responde_404() -> None:
    assert _cliente().post("/consentimientos/no-existe/revocar").status_code == 404


def test_registrar_con_actor_lo_guarda_y_devuelve() -> None:
    client = _cliente()
    actor = {"rol": "ASESOR", "usuario_id": "u-123"}

    creado = client.post("/consentimientos", json={**SOLICITUD, "actor": actor}).json()

    assert creado["actor"] == actor
    assert client.get(f"/consentimientos/{creado['id']}").json()["actor"] == actor


def test_registrar_sin_actor_lo_deja_vacio() -> None:
    assert _cliente().post("/consentimientos", json=SOLICITUD).json()["actor"] is None
