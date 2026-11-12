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


def test_siniestros_parametricos_sin_parametro_es_vacio():
    """KAN-259 Fase 5: `sin-01`/`sin-02`/`sin-03` (de la semilla) no son paramétricos."""
    with _cliente() as c:
        assert c.get("/siniestros/parametricos").json() == []
        assert c.get("/siniestros/parametricos", params={"poliza_id": "pol-01"}).json() == []


def test_siniestros_parametricos_con_repetidos_filtra_por_origen():
    repo = repositorio_con_datos()
    repo.siniestros["sin-par-1"] = {
        "id": "sin-par-1",
        "radicado": "SIN-2026-1001",
        "poliza_id": "pol-02",
        "estado": "APROBADO",
        "asegurado": {"nombre": "Cobertura paramétrica", "documento": "N/A"},
        "cobertura_aplicada": "Cobertura paramétrica de vuelo",
        "entidad_acreedora": "Por confirmar con el asegurado",
        "ocurrido_en": AHORA,
        "notificado_en": AHORA,
        "ubicacion": {"tipo": "VUELO", "clave": "AV9340:2026-11-11"},
        "descripcion": "Siniestro paramétrico automático.",
        "suma_reclamada": {"valor": "500000", "moneda": "COP"},
        "vence_dictamen_en": AHORA,
        "origen": "PARAMETRICO",
        "evento_parametrico_id": "evt-1",
        "evento_valor": 145.0,
        "evento_umbral": 120,
        "evento_variable": None,
        "evento_cancelado": False,
        "vigencia_hasta_cobertura": AHORA,
    }
    with TestClient(create_app(servicio=ServicioSiniestros(repo, lambda: AHORA))) as c:
        respuesta = c.get(
            "/siniestros/parametricos", params={"poliza_id": ["pol-01", "pol-02"]}
        ).json()
    assert [s["id"] for s in respuesta] == ["sin-par-1"]
    assert respuesta[0]["evento_valor"] == 145.0
