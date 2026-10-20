"""Pruebas de los endpoints `GET /perfiles/{cliente_id}` y `POST /ofertas`
con dobles de prueba (sin MongoDB ni Redis reales — la verificación en vivo
se hace con `docker compose`, ver README.md)."""

from fakes import LectorPerfilesFalso, ServicioOfertaFalso
from fastapi.testclient import TestClient

from app.application.servicio_oferta import DatosInsuficientes
from app.main import create_app


def test_obtener_perfil_devuelve_el_perfil_leido() -> None:
    lector = LectorPerfilesFalso(
        perfil={
            "cliente_id": "cliente-1",
            "perfil_version": 1,
            "fuentes": [],
            "leido_de_secundaria": True,
        }
    )

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/cliente-1", params={"operation_time": "1700000001.1"})

    assert r.status_code == 200
    assert r.json()["perfil_version"] == 1
    assert r.json()["leido_de_secundaria"] is True
    assert lector.llamadas == [("cliente-1", "1700000001.1")]


def test_obtener_perfil_inexistente_responde_404_no_5xx() -> None:
    lector = LectorPerfilesFalso(perfil=None)

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/sin-perfil", params={"operation_time": "1.0"})

    assert r.status_code == 404


def test_operation_time_invalido_responde_400_no_5xx() -> None:
    lector = LectorPerfilesFalso()

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/cliente-1", params={"operation_time": "no-es-un-timestamp"})

    assert r.status_code == 400


def test_sin_operation_time_responde_422() -> None:
    lector = LectorPerfilesFalso()

    with TestClient(create_app(lector_perfiles=lector)) as client:
        r = client.get("/perfiles/cliente-1")

    assert r.status_code == 422


OFERTA_EJEMPLO = {
    "id": "oferta-1",
    "tipo": "COMPLETA",
    "prima": {"valor": "94680.00", "moneda": "COP"},
    "rangoPrima": None,
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "desgloseScore": [{"nombre": "historial-crediticio", "peso": 0.30}],
    "fuentes": [],
}


def test_generar_oferta_devuelve_la_oferta_calculada() -> None:
    servicio = ServicioOfertaFalso(oferta=OFERTA_EJEMPLO, score_de_cache=False)

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "1700000001.1",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 200
    assert r.json() == OFERTA_EJEMPLO
    assert servicio.llamadas == [("cliente-1", "1700000001.1", "200000000", None, None)]


def test_generar_oferta_sin_perfil_responde_404_no_5xx() -> None:
    servicio = ServicioOfertaFalso(oferta=None)

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "sin-perfil",
                "operation_time": "1.0",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 404


def test_generar_oferta_con_operation_time_invalido_responde_400_no_5xx() -> None:
    servicio = ServicioOfertaFalso(error=ValueError("formato invalido"))

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "no-es-un-timestamp",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 400


def test_generar_oferta_con_cobertura_invalida_responde_400_no_5xx() -> None:
    from decimal import InvalidOperation

    servicio = ServicioOfertaFalso(error=InvalidOperation("cobertura invalida"))

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "1700000001.1",
                "cobertura": "no-es-un-numero",
            },
        )

    assert r.status_code == 400


def test_generar_oferta_sin_campos_requeridos_responde_422() -> None:
    servicio = ServicioOfertaFalso()

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post("/ofertas", json={"cliente_id": "cliente-1"})

    assert r.status_code == 422


def test_generar_oferta_pasa_plazo_y_direccion_al_servicio() -> None:
    servicio = ServicioOfertaFalso(oferta=OFERTA_EJEMPLO)

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "1700000001.1",
                "cobertura": "200000000",
                "plazo_meses": 240,
                "direccion_inmueble": "Cra 7 # 10-20, Cali",
            },
        )

    assert r.status_code == 200
    assert servicio.llamadas == [
        ("cliente-1", "1700000001.1", "200000000", 240, "Cra 7 # 10-20, Cali")
    ]


def test_generar_oferta_sin_fuente_obligatoria_responde_422_datos_insuficientes() -> None:
    servicio = ServicioOfertaFalso(error=DatosInsuficientes("falta historial"))

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/ofertas",
            json={
                "cliente_id": "cliente-1",
                "operation_time": "1700000001.1",
                "cobertura": "200000000",
            },
        )

    assert r.status_code == 422
    assert r.json() == {"detail": "datos_insuficientes"}


def test_evento_perfil_actualizado_directo_invalida_cache() -> None:
    servicio = ServicioOfertaFalso()

    with TestClient(create_app(servicio_oferta=servicio)) as client:
        r = client.post(
            "/eventos/perfil-actualizado",
            json={
                "eventId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                "tipo": "perfil.actualizado",
                "version": 1,
                "ocurridoEn": "2026-10-18T12:00:00+00:00",
                "datos": {
                    "clienteId": "cliente-1",
                    "perfilVersion": 2,
                    "operationTime": "1700000002.1",
                },
            },
        )

    assert r.status_code == 200
    assert r.json()["estado"] == "ok"
    assert servicio.invalidaciones == [("cliente-1", 2)]


def test_evento_perfil_actualizado_sobre_pubsub_base64_invalida_cache() -> None:
    import base64
    import json

    from fakes import AlmacenClaveValorFalso

    from app.application.cache_score import CacheScore
    from app.application.identificadores import id_opaco

    almacen = AlmacenClaveValorFalso()
    cache = CacheScore(almacen, ttl_segundos=300)
    hash_cliente = id_opaco("cliente-pubsub-1")
    cache.guardar(f"score:{hash_cliente}:v1", {"score_total": 80})
    cache.guardar(f"oferta:{hash_cliente}:1:v1", {"score_total": 80})

    payload_evento = {
        "eventId": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "tipo": "perfil.actualizado",
        "version": 1,
        "ocurridoEn": "2026-10-18T12:00:00+00:00",
        "datos": {
            "clienteId": "cliente-pubsub-1",
            "perfilVersion": 2,
            "operationTime": "1700000002.1",
        },
    }
    sobre_pubsub = {
        "message": {
            "data": base64.b64encode(json.dumps(payload_evento).encode("utf-8")).decode("ascii"),
            "messageId": "1001",
        },
        "subscription": "projects/solventa/subscriptions/rating-perfil-actualizado",
    }

    with TestClient(create_app(servicio_oferta=ServicioOfertaFalso(), cache_score=cache)) as client:
        r = client.post("/eventos/perfil-actualizado", json=sobre_pubsub)

    assert r.status_code == 200
    assert r.json() == {"estado": "ok", "claves_eliminadas": 2}
    assert cache.obtener(f"score:{hash_cliente}:v1") is None
    assert cache.obtener(f"oferta:{hash_cliente}:1:v1") is None


def test_evento_perfil_actualizado_sin_cliente_id_responde_400() -> None:
    with TestClient(create_app(servicio_oferta=ServicioOfertaFalso())) as client:
        r = client.post("/eventos/perfil-actualizado", json={"datos": {}})

    assert r.status_code == 400
