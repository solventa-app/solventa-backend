"""`POST /reconciliar` (Cloud Tasks, DISPARADOR=externo): 200 = resuelto, 503 = reintentar."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as modulo_main
from app.main import create_app
from app.tareas import FuenteSigueDegradadaError

REDIS_URL_SIN_REDIS = "redis://127.0.0.1:1/2"
TRABAJO = {
    "fuente": "historial-crediticio",
    "cliente_id": "cliente-1",
    "consentimiento_id": "consent-1",
    "encolado_en": 1000.0,
}


def _cliente() -> TestClient:
    return TestClient(create_app(redis_url=REDIS_URL_SIN_REDIS, disparador="externo"))


def test_resuelto_responde_200_y_pasa_el_trabajo_a_la_tarea(monkeypatch) -> None:
    recibidos: list[tuple] = []
    monkeypatch.setattr(modulo_main, "reconciliar_fuente", lambda *a: recibidos.append(a))

    with _cliente() as client:
        r = client.post("/reconciliar", json=TRABAJO)

    assert r.status_code == 200
    assert r.json() == {"estado": "resuelta"}
    assert recibidos == [("historial-crediticio", "cliente-1", "consent-1", 1000.0)]


@pytest.mark.parametrize(
    "error",
    [FuenteSigueDegradadaError("f"), httpx.ConnectError("sin red"), RuntimeError("otra cosa")],
)
def test_si_hay_que_reintentar_responde_503_sin_exponer_el_detalle(monkeypatch, error) -> None:
    def falla(*_: object) -> None:
        raise error

    monkeypatch.setattr(modulo_main, "reconciliar_fuente", falla)

    with _cliente() as client:
        r = client.post("/reconciliar", json=TRABAJO)

    assert r.status_code == 503
    assert r.json() == {"detail": "reintentar"}


def test_un_trabajo_incompleto_responde_422() -> None:
    with _cliente() as client:
        assert client.post("/reconciliar", json={"fuente": "x"}).status_code == 422


def test_con_disparador_externo_no_se_arranca_el_trabajador_de_rq() -> None:
    with _cliente() as client:
        assert client.app.state.trabajador is None


def test_con_disparador_bucle_si_hay_trabajador() -> None:
    with TestClient(create_app(redis_url=REDIS_URL_SIN_REDIS, disparador="bucle")) as client:
        assert client.app.state.trabajador is not None


def test_disparador_desconocido_falla_claro() -> None:
    with pytest.raises(ValueError, match="DISPARADOR"):
        create_app(redis_url=REDIS_URL_SIN_REDIS, disparador="magia")
