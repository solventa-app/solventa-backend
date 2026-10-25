"""`ClienteAcl` de POLICY contra respuestas HTTP simuladas (sin levantar el ACL Worker)."""

import json
import logging

import httpx

from app.application.cliente_acl import (
    AdjuntoAviso,
    ClienteAcl,
    SolicitudFirma,
    SolicitudNotificacion,
)

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


AVISO = SolicitudNotificacion(
    "poliza-emitida-pz-1", "poliza-emitida", "usuario-1", {"numero_poliza": "SOL-2026-000001"}
)


def test_notificar_manda_la_plantilla_el_id_opaco_y_los_datos() -> None:
    vistas: list[dict] = []
    cuerpo = {"estado": "enviada", "referencia": "nt-1", "motivo": None}

    resultado = _cliente(lambda: httpx.Response(200, json=cuerpo), vistas).notificar(AVISO)

    assert resultado.estado == "enviada"
    assert vistas == [
        {
            "id_notificacion": "poliza-emitida-pz-1",
            "plantilla": "poliza-emitida",
            "destinatario_id": "usuario-1",
            "datos": {"numero_poliza": "SOL-2026-000001"},
        }
    ]


def test_notificar_con_el_acl_caido_queda_pendiente_sin_lanzar() -> None:
    assert _cliente(lambda: httpx.Response(503)).notificar(AVISO).estado == "pendiente"
    assert _cliente(lambda: httpx.Response(200, content=b"no json")).notificar(AVISO).estado == (
        "pendiente"
    )
    for cuerpo in ({}, [], "texto"):
        resultado = _cliente(lambda cuerpo=cuerpo: httpx.Response(200, json=cuerpo)).notificar(
            AVISO
        )
        assert resultado.estado == "pendiente"


# ---------- contacto y PDF adjunto (HU-W03, bloque 8, W03-16)

CORREO = "maria.gomez@ejemplo.co"
ADJUNTO = AdjuntoAviso("poliza-SOL-2026-000001.pdf", "application/pdf", "JVBERi0xLjQ=")


def test_notificar_manda_el_correo_y_el_pdf_adjunto() -> None:
    vistas: list[dict] = []
    aviso = SolicitudNotificacion(
        "poliza-emitida-pz-1", "poliza-emitida", "usuario-1", {"numero_poliza": "SOL-2026-000001"},
        correo=CORREO, adjuntos=[ADJUNTO],
    )  # fmt: skip

    resultado = _cliente(
        lambda: httpx.Response(200, json={"estado": "enviada", "referencia": "nt-1"}), vistas
    ).notificar(aviso)

    assert resultado.estado == "enviada"
    assert vistas[0]["correo"] == CORREO
    assert vistas[0]["adjuntos"] == [
        {"nombre": ADJUNTO.nombre, "tipo": "application/pdf", "contenido_base64": "JVBERi0xLjQ="}
    ]
    assert "telefono" not in vistas[0]


def test_notificar_solo_con_telefono_no_manda_adjuntos() -> None:
    vistas: list[dict] = []
    aviso = SolicitudNotificacion(
        "poliza-emitida-pz-1", "poliza-emitida", "usuario-1", {}, telefono="+573001234567"
    )

    _cliente(lambda: httpx.Response(200, json={"estado": "enviada"}), vistas).notificar(aviso)

    assert vistas[0]["telefono"] == "+573001234567"
    assert "correo" not in vistas[0] and "adjuntos" not in vistas[0]


def test_un_aviso_sin_contacto_ni_adjunto_lleva_el_cuerpo_de_siempre() -> None:
    vistas: list[dict] = []

    _cliente(lambda: httpx.Response(200, json={"estado": "enviada"}), vistas).notificar(AVISO)

    assert set(vistas[0]) == {"id_notificacion", "plantilla", "destinatario_id", "datos"}


def test_un_422_del_acl_es_rechazada_y_no_se_reintenta() -> None:
    aviso = SolicitudNotificacion(
        "poliza-emitida-pz-1", "poliza-emitida", "usuario-1", {}, correo=CORREO, adjuntos=[ADJUNTO]
    )

    resultado = _cliente(lambda: httpx.Response(422, json={"detail": "x"})).notificar(aviso)

    assert (resultado.estado, resultado.motivo) == ("rechazada", "solicitud_invalida")


def test_un_5xx_del_acl_con_adjunto_sigue_siendo_pendiente() -> None:
    aviso = SolicitudNotificacion(
        "poliza-emitida-pz-1", "poliza-emitida", "usuario-1", {}, correo=CORREO, adjuntos=[ADJUNTO]
    )

    assert _cliente(lambda: httpx.Response(503)).notificar(aviso).estado == "pendiente"


def test_el_cliente_del_acl_no_escribe_el_contacto_en_los_logs(caplog) -> None:
    aviso = SolicitudNotificacion(
        "poliza-emitida-pz-1", "poliza-emitida", "usuario-1", {}, correo=CORREO, adjuntos=[ADJUNTO]
    )
    with caplog.at_level(logging.DEBUG):
        _cliente(lambda: httpx.Response(422, json={"detail": "x"})).notificar(aviso)
        _cliente(lambda: httpx.Response(503)).notificar(aviso)

    assert CORREO not in caplog.text and "JVBERi0xLjQ" not in caplog.text
