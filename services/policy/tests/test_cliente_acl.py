"""`ClienteAcl` de POLICY contra respuestas HTTP simuladas (sin levantar el ACL Worker)."""

import json

import httpx

from app.application.cliente_acl import ClienteAcl, SolicitudFirma

SOLICITUD = SolicitudFirma("p-1", "a" * 64, "v1", "usuario-1")


def _cliente(responder, vistas: list[dict] | None = None) -> ClienteAcl:
    def manejar(peticion: httpx.Request) -> httpx.Response:
        if vistas is not None:
            vistas.append(json.loads(peticion.content))
        return responder()

    cliente = ClienteAcl("http://acl", 1.0)
    cliente._cliente = httpx.Client(base_url="http://acl", transport=httpx.MockTransport(manejar))
    return cliente


def test_manda_solo_el_hash_y_devuelve_la_evidencia() -> None:
    vistas: list[dict] = []
    cuerpo = {"estado": "firmada", "referencia": "fe-1", "firmada_en": "2026-10-22T12:00:00Z"}

    resultado = _cliente(lambda: httpx.Response(200, json=cuerpo), vistas).firmar(SOLICITUD)

    assert resultado.estado == "firmada"
    assert resultado.referencia == "fe-1"
    assert vistas == [
        {
            "id_firma": "p-1",
            "documento_hash": "a" * 64,
            "version_documento": "v1",
            "firmante_id": "usuario-1",
        }
    ]


def test_un_5xx_del_acl_queda_pendiente_sin_lanzar() -> None:
    resultado = _cliente(lambda: httpx.Response(503)).firmar(SOLICITUD)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == "acl_no_disponible"


def test_una_respuesta_sin_estado_queda_pendiente_sin_lanzar() -> None:
    for cuerpo in ({}, [], "texto"):
        resultado = _cliente(lambda cuerpo=cuerpo: httpx.Response(200, json=cuerpo)).firmar(
            SOLICITUD
        )

        assert resultado.estado == "pendiente"


def test_una_respuesta_que_no_es_json_queda_pendiente_sin_lanzar() -> None:
    resultado = _cliente(lambda: httpx.Response(200, content=b"no json")).firmar(SOLICITUD)

    assert resultado.estado == "pendiente"
