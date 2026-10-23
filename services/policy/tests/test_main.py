"""Endpoints de POLICY con dobles de prueba (sin PostgreSQL ni RATING reales: la verificación con
los reales se hace en vivo con `docker compose`)."""

from fakes import RatingFalso, RepositorioPropuestasFalso, oferta_de_rating
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.servicio_propuestas import ServicioPropuestas
from app.main import create_app


def _cliente(rating: RatingFalso | None = None) -> TestClient:
    servicio = ServicioPropuestas(RepositorioPropuestasFalso(), rating or RatingFalso())
    config = Configuracion("", "http://rating", 1.0, "ninguna")
    return TestClient(create_app(config=config, servicio_propuestas=servicio))


def test_health() -> None:
    with _cliente() as client:
        assert client.get("/health").json() == {"servicio": "policy", "estado": "ok"}


def test_post_propuestas_acepta_la_oferta_y_get_la_devuelve() -> None:
    with _cliente() as client:
        creada = client.post(
            "/propuestas", json={"oferta_id": "oferta-1", "actor_usuario_id": "u1"}
        )
        leida = client.get(f"/propuestas/{creada.json()['id']}")

    assert creada.status_code == 200
    assert creada.json()["estado"] == "ACEPTADA"
    assert creada.json()["prima"] == {"valor": "185000", "moneda": "COP"}
    assert leida.status_code == 200
    assert leida.json()["id"] == creada.json()["id"]


def test_post_propuestas_dos_veces_devuelve_la_misma() -> None:
    with _cliente() as client:
        primera = client.post("/propuestas", json={"oferta_id": "oferta-1"}).json()
        segunda = client.post("/propuestas", json={"oferta_id": "oferta-1"}).json()

    assert primera["id"] == segunda["id"]


def test_oferta_preliminar_responde_422_con_el_motivo() -> None:
    oferta = oferta_de_rating(tipo="PRELIMINAR", prima=None)
    with _cliente(RatingFalso({"oferta-1": oferta})) as client:
        r = client.post("/propuestas", json={"oferta_id": "oferta-1"})

    assert r.status_code == 422
    assert r.json()["detail"] == "OFERTA_PRELIMINAR"


def test_oferta_desconocida_responde_404() -> None:
    with _cliente() as client:
        r = client.post("/propuestas", json={"oferta_id": "no-existe"})

    assert r.status_code == 404
    assert r.json()["detail"] == "OFERTA_NO_ENCONTRADA"


def test_rating_caido_responde_503_para_que_el_bff_ofrezca_reintentar() -> None:
    with _cliente(RatingFalso(cae=True)) as client:
        r = client.post("/propuestas", json={"oferta_id": "oferta-1"})

    assert r.status_code == 503
    assert r.json()["detail"] == "RATING_NO_DISPONIBLE"


def test_propuesta_desconocida_responde_404() -> None:
    with _cliente() as client:
        assert client.get("/propuestas/no-existe").status_code == 404
