from fakes import AHORA, repositorio_con_datos
from fastapi.testclient import TestClient

from app.application.servicio_siniestros import ServicioSiniestros
from app.main import create_app


def _cliente():
    return TestClient(
        create_app(servicio=ServicioSiniestros(repositorio_con_datos(), lambda: AHORA))
    )


def test_health_y_metricas():
    with _cliente() as c:
        assert c.get("/health").json() == {"servicio": "claims", "estado": "ok"}
        assert c.get("/metrics").status_code == 200


def test_bandeja_y_filtro():
    with _cliente() as c:
        assert [s["id"] for s in c.get("/siniestros").json()] == ["sin-02", "sin-01", "sin-03"]
        assert [s["id"] for s in c.get("/siniestros", params={"estado": "APROBADO"}).json()] == [
            "sin-03"
        ]
        assert c.get("/siniestros", params={"estado": "DERIVADO"}).status_code == 422


def test_detalle_y_no_encontrado():
    with _cliente() as c:
        r = c.get("/siniestros/sin-01")
        assert r.status_code == 200
        assert r.json()["notificado_en"].startswith("2026-10-19")
        assert c.get("/siniestros/sin-99").status_code == 404


def test_peritos_del_caso():
    with _cliente() as c:
        peritos = c.get("/siniestros/sin-01/peritos").json()
        assert peritos[0]["vencimiento_informe"] == "2026-11-03T17:00:00-05:00"
