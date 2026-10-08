from fakes import LectorPerfilesFalso
from fastapi.testclient import TestClient

from app.main import create_app

# Se inyecta un LectorPerfiles falso para que estas pruebas no necesiten un
# replica set de MongoDB real solo para comprobar /health y /metrics.
LECTOR_FALSO = LectorPerfilesFalso()


def test_health_responde_ok() -> None:
    with TestClient(create_app(lector_perfiles=LECTOR_FALSO)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["estado"] == "ok"


def test_metrics_expuestas() -> None:
    with TestClient(create_app(lector_perfiles=LECTOR_FALSO)) as client:
        assert client.get("/metrics").status_code == 200
