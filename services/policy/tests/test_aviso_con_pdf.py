"""El aviso de póliza emitida con el PDF adjunto (HU-W03, bloque 8, W03-16 a W03-18).

Por correo lleva el PDF; con solo teléfono sale sin PDF; sin contacto se omite (`SIN_CONTACTO`) y
no se reintenta; si AUTH no responde NO se asume que no hay contacto y se reintenta."""

import base64
import logging

import pytest
from fakes import (
    AclFalso,
    BusFalso,
    ContactosFalso,
    GeneradorDeDocumentoFalso,
    NotificadorFalso,
    PaymentsFalso,
    PlanificadorFalso,
    RatingFalso,
    RepositorioPropuestasFalso,
    UnderFalso,
)

from app.application.cliente_auth import Contacto
from app.application.planificador import EmisionPendiente
from app.application.servicio_emision import (
    MOTIVO_AVISO_RECHAZADO,
    MOTIVO_SIN_CONTACTO,
    ServicioEmision,
)
from app.application.servicio_firma import ServicioFirma
from app.application.servicio_propuestas import ServicioPropuestas

CORREO = "maria.gomez@ejemplo.co"
TELEFONO = "+573001234567"
NOMBRE = "María Fernanda Gómez"


def _armar(contactos=None, generador=None, notificador=None, con_contactos=True):
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    ServicioFirma(repositorio, AclFalso()).firmar(propuesta["id"], True, "v1", "usuario-1")
    contactos = contactos or ContactosFalso(Contacto(correo=CORREO, telefono=TELEFONO))
    generador = generador or GeneradorDeDocumentoFalso()
    notificador = notificador or NotificadorFalso()
    extra = {"contactos": contactos, "generador_documento": generador} if con_contactos else {}
    servicio = ServicioEmision(
        repositorio, UnderFalso(), PaymentsFalso(), BusFalso(), PlanificadorFalso(), notificador,
        "tok_recaudo", 10.0, 12, **extra,
    )  # fmt: skip
    return servicio, repositorio, contactos, generador, notificador, propuesta["id"]


def _poliza(repositorio, propuesta_id):
    return repositorio.obtener_poliza(repositorio.obtener(propuesta_id)["poliza_id"])


# ---------- por correo: con el PDF


def test_con_correo_el_aviso_lleva_el_pdf_adjunto() -> None:
    servicio, repositorio, contactos, generador, notificador, pid = _armar()

    assert servicio.emitir(pid) == "EMITIDA"

    solicitud = notificador.solicitudes[0]
    assert solicitud.correo == CORREO and solicitud.telefono is None
    assert len(solicitud.adjuntos) == 1
    adjunto = solicitud.adjuntos[0]
    assert adjunto.tipo == "application/pdf" and adjunto.nombre.startswith("poliza-SOL-")
    assert base64.b64decode(adjunto.contenido_base64) == GeneradorDeDocumentoFalso.CONTENIDO
    assert _poliza(repositorio, pid)["notificada_en"] is not None


def test_el_pdf_se_genera_con_el_nombre_que_da_auth_y_el_nombre_no_se_guarda() -> None:
    servicio, repositorio, contactos, generador, notificador, pid = _armar()

    servicio.emitir(pid)

    assert generador.llamadas == [("poliza-1", NOMBRE)]
    assert contactos.consultas_de_contacto == ["usuario-1"]
    assert contactos.consultas_de_nombre == ["usuario-1"]
    # Ni POLICY guarda el nombre ni el contacto: no aparecen en nada de lo que persiste.
    persistido = str(vars(repositorio))
    assert NOMBRE not in persistido and CORREO not in persistido and TELEFONO not in persistido


def test_con_correo_y_telefono_va_por_correo_y_no_se_manda_el_telefono() -> None:
    servicio, *_, notificador, pid = _armar()

    servicio.emitir(pid)

    assert notificador.solicitudes[0].telefono is None


def test_la_identidad_del_aviso_no_cambia() -> None:
    servicio, repositorio, *_, notificador, pid = _armar()

    servicio.emitir(pid)

    solicitud = notificador.solicitudes[0]
    assert solicitud.id_notificacion == "poliza-emitida-poliza-1"
    assert solicitud.destinatario_id == "usuario-1"
    assert solicitud.datos["enlace_documento"] == "/polizas/poliza-1/documento"


# ---------- solo teléfono: sin PDF


def test_con_solo_telefono_el_aviso_sale_sin_pdf() -> None:
    contactos = ContactosFalso(Contacto(telefono=TELEFONO))
    servicio, repositorio, _, generador, notificador, pid = _armar(contactos=contactos)

    servicio.emitir(pid)

    solicitud = notificador.solicitudes[0]
    assert solicitud.telefono == TELEFONO and solicitud.correo is None
    assert solicitud.adjuntos == []
    assert generador.llamadas == [] and contactos.consultas_de_nombre == []
    assert _poliza(repositorio, pid)["notificada_en"] is not None


# ---------- sin contacto: se omite y no se reintenta (W03-18)


@pytest.mark.parametrize("contacto", [None, Contacto(), Contacto(correo="", telefono=None)])
def test_sin_contacto_la_poliza_se_emite_y_el_aviso_se_omite(contacto) -> None:
    servicio, repositorio, _, generador, notificador, pid = _armar(
        contactos=ContactosFalso(contacto)
    )

    assert servicio.emitir(pid) == "EMITIDA"

    poliza = _poliza(repositorio, pid)
    assert poliza["aviso_omitido_motivo"] == MOTIVO_SIN_CONTACTO
    assert poliza["notificada_en"] is None
    assert notificador.solicitudes == [] and generador.llamadas == []


def test_un_aviso_omitido_no_se_reintenta_para_siempre() -> None:
    contactos = ContactosFalso(None)
    servicio, repositorio, _, _, notificador, pid = _armar(contactos=contactos)
    servicio.emitir(pid)

    servicio.emitir(pid)
    servicio.emitir(pid)

    assert contactos.consultas_de_contacto == ["usuario-1"]  # AUTH se consultó una sola vez
    assert notificador.solicitudes == []


# ---------- AUTH no responde: no se asume que no hay contacto


def test_si_auth_no_responde_el_aviso_se_reintenta_y_no_se_omite() -> None:
    contactos = ContactosFalso(Contacto(correo=CORREO), cae=1)
    servicio, repositorio, _, _, notificador, pid = _armar(contactos=contactos)

    with pytest.raises(EmisionPendiente):
        servicio.emitir(pid)

    poliza = _poliza(repositorio, pid)
    assert poliza["aviso_omitido_motivo"] is None and poliza["notificada_en"] is None
    assert repositorio.obtener(pid)["estado"] == "EMITIDA"  # la póliza ya está emitida
    assert notificador.solicitudes == []

    assert servicio.emitir(pid) == "EMITIDA"  # AUTH vuelve: el aviso sale
    assert _poliza(repositorio, pid)["notificada_en"] is not None
    assert notificador.solicitudes[0].correo == CORREO


def test_si_auth_cae_al_pedir_el_nombre_el_aviso_se_reintenta() -> None:
    contactos = ContactosFalso(Contacto(correo=CORREO), cae_en_nombre=True)
    servicio, repositorio, _, generador, notificador, pid = _armar(contactos=contactos)

    with pytest.raises(EmisionPendiente):
        servicio.emitir(pid)

    assert generador.llamadas == [] and notificador.solicitudes == []
    assert _poliza(repositorio, pid)["aviso_omitido_motivo"] is None


def test_si_no_se_puede_generar_el_pdf_el_aviso_se_reintenta() -> None:
    servicio, repositorio, _, _, notificador, pid = _armar(
        generador=GeneradorDeDocumentoFalso(devuelve_none=True)
    )

    with pytest.raises(EmisionPendiente):
        servicio.emitir(pid)

    assert notificador.solicitudes == []


# ---------- el ACL


def test_si_el_acl_no_responde_el_aviso_se_reintenta_con_el_mismo_id() -> None:
    notificador = NotificadorFalso(["pendiente", "enviada"])
    servicio, repositorio, _, generador, _, pid = _armar(notificador=notificador)

    with pytest.raises(EmisionPendiente):
        servicio.emitir(pid)
    assert servicio.emitir(pid) == "EMITIDA"

    assert [s.id_notificacion for s in notificador.solicitudes] == ["poliza-emitida-poliza-1"] * 2
    assert len(generador.llamadas) == 2  # el PDF no se guarda: se vuelve a generar
    assert _poliza(repositorio, pid)["notificada_en"] is not None


def test_si_el_acl_rechaza_la_solicitud_se_omite_y_no_se_reintenta() -> None:
    notificador = NotificadorFalso(["rechazada"])
    servicio, repositorio, *_, pid = _armar(notificador=notificador)

    assert servicio.emitir(pid) == "EMITIDA"  # no lanza: reintentar daría el mismo rechazo

    assert _poliza(repositorio, pid)["aviso_omitido_motivo"] == MOTIVO_AVISO_RECHAZADO
    servicio.emitir(pid)
    assert len(notificador.solicitudes) == 1


# ---------- compatibilidad y reglas


def test_sin_auth_configurado_el_aviso_sale_como_antes() -> None:
    servicio, repositorio, *_, notificador, pid = _armar(con_contactos=False)

    servicio.emitir(pid)

    solicitud = notificador.solicitudes[0]
    assert solicitud.correo is None and solicitud.telefono is None and solicitud.adjuntos == []
    assert _poliza(repositorio, pid)["notificada_en"] is not None


def test_avisar_con_pdf_exige_un_generador_de_documentos() -> None:
    with pytest.raises(ValueError, match="generador"):
        ServicioEmision(
            RepositorioPropuestasFalso(), UnderFalso(), PaymentsFalso(), BusFalso(),
            PlanificadorFalso(), NotificadorFalso(), "tok", 10.0, 12,
            contactos=ContactosFalso(),
        )  # fmt: skip


def test_reabrir_una_poliza_por_reconciliacion_tambien_avisa_con_el_pdf() -> None:
    servicio, repositorio, _, _, notificador, pid = _armar()
    repositorio.cambiar_estado(pid, "PAGO_RECHAZADO", ("EN_EMISION",), "fondos_insuficientes")

    assert servicio.reconciliar_cobro(pid) == "EMITIDA"

    assert notificador.solicitudes[0].adjuntos[0].tipo == "application/pdf"


# ---------- datos personales fuera de los logs


def test_los_logs_no_llevan_el_contacto_el_nombre_ni_el_pdf(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        for contacto in (
            Contacto(correo=CORREO, telefono=TELEFONO),
            Contacto(telefono=TELEFONO),
            None,
        ):
            servicio, *_, pid = _armar(contactos=ContactosFalso(contacto))
            servicio.emitir(pid)
        servicio, *_, pid = _armar(contactos=ContactosFalso(Contacto(correo=CORREO), cae=1))
        with pytest.raises(EmisionPendiente):
            servicio.emitir(pid)

    pdf_b64 = base64.b64encode(GeneradorDeDocumentoFalso.CONTENIDO).decode("ascii")
    for dato in (CORREO, "maria.gomez", TELEFONO, "3001234567", NOMBRE, "Gómez", pdf_b64):
        assert dato not in caplog.text
    assert "aviso omitido motivo=SIN_CONTACTO" in caplog.text  # sí se deja el rastro, sin datos
