from fastapi.testclient import TestClient

from app.main import app

# Sin "with": el lifespan no se ejecuta, así que /health y /metrics no
# necesitan PostgreSQL ni el ACL Worker reales (mismo patrón de
# acl-worker/tests/test_health.py).
client = TestClient(app)


def test_health_responde_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok"


def test_metrics_expuestas():
    assert client.get("/metrics").status_code == 200
