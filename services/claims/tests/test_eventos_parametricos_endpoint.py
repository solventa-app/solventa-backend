"""KAN-259 (HU-M04, Fase 4): `POST /eventos/parametricos` en CLAIMS."""

import base64
import json

from fakes import AHORA, PaymentsFalso, PolicyParametricoFalso, RepositorioFalso
from fastapi.testclient import TestClient

from app.application.servicio_parametricos import ServicioParametricos
from app.main import create_app


def _evento_vuelo_valido() -> dict:
    return {
        "eventId": "11111111-1111-1111-1111-111111111111",
        "tipo": "evento.parametrico.recibido",
        "version": 1,
        "ocurridoEn": "2026-11-11T08:00:05+00:00",
        "datos": {
            "tipo": "VUELO",
            "clave": "AV9340:2026-11-11",
            "valor": 145.0,
            "medidoEn": "2026-11-11T08:00:00+00:00",
            "fuente": "aviationstack",
            "cancelado": False,
        },
    }


def _cobertura_vuelo() -> dict:
    return {
        "id": "cob-1",
        "poliza_id": "poliza-1",
        "tipo": "VUELO",
        "monto_fijo": {"valor": "500000", "moneda": "COP"},
        "vigencia_desde": "2026-01-01T00:00:00+00:00",
        "vigencia_hasta": "2027-01-01T00:00:00+00:00",
        "estado": "VIGENTE",
        "numero_vuelo": "AV9340",
        "fecha_salida": "2026-11-11",
        "aeropuerto_origen": "BOG",
        "umbral_retraso_min": 120,
        "latitud": None,
        "longitud": None,
        "variable": None,
        "umbral": None,
        "ventana_horas": None,
    }


def _cliente(policy=None, payments=None, repo=None) -> TestClient:
    repo = repo or RepositorioFalso()
    parametricos = ServicioParametricos(
        repo,
        policy or PolicyParametricoFalso(),
        payments or PaymentsFalso(),
        bus=None,
        reloj=lambda: AHORA,
    )
    return TestClient(create_app(servicio=object(), parametricos=parametricos))


def test_evento_que_califica_crea_siniestro_y_responde_200():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]}
    )
    with _cliente(policy=policy) as cliente:
        respuesta = cliente.post("/eventos/parametricos", json=_evento_vuelo_valido())
    assert respuesta.status_code == 200
    assert respuesta.json() == {"creados": 1, "revisadas": 1}


def test_evento_sin_polizas_que_califiquen_responde_200_sin_crear():
    with _cliente() as cliente:
        respuesta = cliente.post("/eventos/parametricos", json=_evento_vuelo_valido())
    assert respuesta.status_code == 200
    assert respuesta.json() == {"creados": 0, "revisadas": 0}


def test_evento_invalido_contra_el_esquema_responde_400():
    evento = _evento_vuelo_valido()
    del evento["datos"]["fuente"]
    with _cliente() as cliente:
        respuesta = cliente.post("/eventos/parametricos", json=evento)
    assert respuesta.status_code == 400


def test_sobre_de_pubsub_se_decodifica_antes_de_validar():
    evento = _evento_vuelo_valido()
    data_b64 = base64.b64encode(json.dumps(evento).encode("utf-8")).decode("ascii")
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]}
    )
    with _cliente(policy=policy) as cliente:
        respuesta = cliente.post(
            "/eventos/parametricos", json={"message": {"data": data_b64}}
        )
    assert respuesta.status_code == 200
    assert respuesta.json()["creados"] == 1


def test_sobre_de_pubsub_con_data_invalida_responde_400():
    with _cliente() as cliente:
        respuesta = cliente.post(
            "/eventos/parametricos", json={"message": {"data": "no-es-base64-valido!!"}}
        )
    assert respuesta.status_code == 400


def test_fallo_interno_responde_500_para_que_pubsub_reintente():
    policy = PolicyParametricoFalso(falla=RuntimeError("timeout"))
    cliente = TestClient(
        create_app(
            servicio=object(),
            parametricos=ServicioParametricos(
                RepositorioFalso(), policy, PaymentsFalso(), bus=None, reloj=lambda: AHORA
            ),
        ),
        raise_server_exceptions=False,
    )
    with cliente:
        respuesta = cliente.post("/eventos/parametricos", json=_evento_vuelo_valido())
    assert respuesta.status_code == 500
