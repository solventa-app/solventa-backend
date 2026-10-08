"""Pruebas de los endpoints del Consolidador. `redis_url` apunta a un puerto
sin nadie escuchando (conexión rechazada, falla rápida): el trabajador de
fondo falla al conectar, pero el contenedor sigue respondiendo /health y
/metrics con normalidad (nunca tumba el proceso completo)."""

from fastapi.testclient import TestClient

from app.main import create_app

REDIS_URL_SIN_REDIS = "redis://127.0.0.1:1/2"


def test_health_responde_ok() -> None:
    with TestClient(create_app(redis_url=REDIS_URL_SIN_REDIS)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["estado"] == "ok"


def test_metrics_expuestas() -> None:
    with TestClient(create_app(redis_url=REDIS_URL_SIN_REDIS)) as client:
        assert client.get("/metrics").status_code == 200
