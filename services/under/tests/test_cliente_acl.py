"""`ClienteAcl` de UNDER contra respuestas HTTP simuladas (sin levantar el ACL Worker)."""

import json

import httpx

from app.application.cliente_acl import ClienteAcl


def _cliente(responder, vistas: list[dict] | None = None) -> ClienteAcl:
    def manejar(peticion: httpx.Request) -> httpx.Response:
        if vistas is not None:
            vistas.append(json.loads(peticion.content))
        return responder()

    cliente = ClienteAcl("http://acl", 1.0)
    cliente._cliente = httpx.Client(base_url="http://acl", transport=httpx.MockTransport(manejar))
    return cliente


def test_manda_solo_el_id_de_la_propuesta_y_el_cliente_opaco() -> None:
    vistas: list[dict] = []
    cuerpo = {"estado": "aprobada", "referencia": "kyc-1", "motivo": None}

    resultado = _cliente(lambda: httpx.Response(200, json=cuerpo), vistas).verificar_kyc(
        "p-1", "c-1"
    )

    assert resultado.estado == "aprobada"
    assert resultado.referencia == "kyc-1"
    assert vistas == [{"id_verificacion": "p-1", "cliente_id": "c-1"}]


def test_un_5xx_del_acl_queda_pendiente_sin_lanzar() -> None:
    resultado = _cliente(lambda: httpx.Response(503)).verificar_kyc("p-1", "c-1")

    assert resultado.estado == "pendiente"
    assert resultado.motivo == "acl_no_disponible"


def test_una_respuesta_sin_estado_queda_pendiente_sin_lanzar() -> None:
    for cuerpo in ({}, [], "texto"):
        resultado = _cliente(lambda cuerpo=cuerpo: httpx.Response(200, json=cuerpo)).verificar_kyc(
            "p-1", "c-1"
        )

        assert resultado.estado == "pendiente"


def test_una_respuesta_que_no_es_json_queda_pendiente_sin_lanzar() -> None:
    resultado = _cliente(lambda: httpx.Response(200, content=b"no json")).verificar_kyc(
        "p-1", "c-1"
    )

    assert resultado.estado == "pendiente"
