from fakes import RepositorioPerfilesFalso
from fastapi.testclient import TestClient

from app.application.bus_eventos import BusEventosLog
from app.application.servicio_perfiles import ServicioPerfiles
from app.main import create_app

# Se inyecta un ServicioPerfiles con dobles de prueba (ver fakes.py) para que
# estas pruebas no necesiten un MongoDB real solo para comprobar /health y
# /metrics (mismo motivo por el que acl-worker usa una Configuracion con
# proveedores inalcanzables en sus pruebas).
SERVICIO_FALSO = ServicioPerfiles(RepositorioPerfilesFalso(), BusEventosLog())


def test_health_responde_ok() -> None:
    with TestClient(create_app(servicio_perfiles=SERVICIO_FALSO)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["estado"] == "ok"


def test_metrics_expuestas() -> None:
    with TestClient(create_app(servicio_perfiles=SERVICIO_FALSO)) as client:
        assert client.get("/metrics").status_code == 200
