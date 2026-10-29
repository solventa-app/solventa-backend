"""Firma y pago con el asesor o en la sesión del CLIENTE (HU-W03, bloque 9), con dobles en memoria:
código, «Emitir»/«Firmar», enlace de 30 min, autorización doble, firma en tres modos con la imagen
por el almacenamiento, pago, emisión en la misma respuesta, avisos, purga y listas."""

import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from fakes import (
    AclFalso,
    BusFalso,
    GeneradorDeDocumentoFalso,
    NotificadorFalso,
    PaymentsFalso,
    PlanificadorFalso,
    RatingFalso,
    UnderFalso,
    oferta_de_rating,
)
from fakes_bloque9 import (
    AlmacenamientoFalso,
    ConsentimientosFalso,
    PagosFalso,
    RepositorioBloque9Falso,
    png,
)
from fastapi.testclient import TestClient

from app.application.avisos import AvisosNulos
from app.application.configuracion import Configuracion
from app.application.documento_poliza import generar_borrador, generar_pdf
from app.application.huellas import clave_de_huellas, enmascarar_correo
from app.application.imagen_firma import ImagenInvalida, validar
from app.application.servicio_emision import ServicioEmision
from app.application.servicio_firma_y_pago import (
    Autorizacion,
    ErrorFlujo,
    Reglas,
    ServicioFirmaYPago,
)
from app.application.servicio_polizas import ServicioPolizas
from app.application.servicio_propuestas import ServicioPropuestas
from app.application.servicio_purga import ServicioPurga
from app.main import create_app

CONTACTO = {"nombre": "Camila Rojas", "correo": "camila.rojas@correo.com"}
TITULAR = "cliente-opaco"  # el `clienteId` de la oferta de prueba (HMAC del documento)


class Reloj:
    def __init__(self) -> None:
        self.ahora = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.ahora


class Entorno:
    def __init__(self, under: UnderFalso | None = None) -> None:
        self.repo = RepositorioBloque9Falso()
        self.rating = RatingFalso(
            {"oferta-1": oferta_de_rating(), "oferta-2": oferta_de_rating(id="oferta-2")}
        )
        self.under = under or UnderFalso()
        self.acl = AclFalso()
        self.notificador = NotificadorFalso()
        self.almacen = AlmacenamientoFalso()
        self.pagos = PagosFalso()
        self.avisos = AvisosNulos()
        self.planificador = PlanificadorFalso()
        self.reloj = Reloj()
        self.propuestas = ServicioPropuestas(self.repo, self.rating)
        self.emision = ServicioEmision(
            self.repo,
            self.under,
            PaymentsFalso(),
            BusFalso(),
            self.planificador,
            self.notificador,
            "tok_recaudo",
            generador_documento=GeneradorDeDocumentoFalso(),
            avisos=self.avisos,
        )
        self.servicio = ServicioFirmaYPago(
            self.repo,
            self.under,
            self.acl,
            self.notificador,
            self.almacen,
            self.pagos,
            self.emision.emitir_pagada,
            Reglas(clave_huellas=clave_de_huellas("clave-de-prueba"), url_front="http://front"),
            self.avisos,
            reloj=self.reloj,
        )

    # --- ayudas
    def aceptar_como_asesor(self, oferta_id: str = "oferta-1") -> dict:
        return self.propuestas.aceptar(oferta_id, "asesor-1", "ASESOR", dict(CONTACTO))

    def aceptar_como_cliente(self) -> dict:
        return self.propuestas.aceptar("oferta-1", "usuario-cliente", "CLIENTE")

    def codigo_enviado(self) -> str:
        codigos = [s for s in self.notificador.solicitudes if s.plantilla == "codigo-verificacion"]
        return codigos[-1].datos["codigo"]

    def token_enviado(self) -> str:
        enlace = self.notificador.solicitudes[-1].datos["enlace"]
        return re.search(r"#t=(.+)$", enlace).group(1)

    def hasta_en_firma(self) -> tuple[dict, str]:
        propuesta = self.aceptar_como_asesor()
        self.servicio.enviar_codigo(propuesta["id"])
        self.servicio.verificar_codigo(propuesta["id"], self.codigo_enviado())
        self.servicio.emitir(propuesta["id"], "ASESOR")
        return propuesta, self.token_enviado()

    def firmar_nombre(self, propuesta_id: str, auth: Autorizacion):
        return self.servicio.firmar(propuesta_id, auth, "v1", True, True, "NOMBRE", None, "Camila")


@pytest.fixture
def e() -> Entorno:
    return Entorno()


def _motivo(funcion, *args, **kwargs) -> ErrorFlujo:
    with pytest.raises(ErrorFlujo) as error:
        funcion(*args, **kwargs)
    return error.value


# ---------- aceptar con el contacto según el rol (W03-25)


def test_el_asesor_deja_el_contacto_cifrado_y_el_cliente_lo_toma_de_auth(e) -> None:
    del_asesor = e.aceptar_como_asesor()
    del_cliente = e.propuestas.aceptar("oferta-2", "usuario-cliente", "CLIENTE")

    assert del_asesor["origen_contacto"] == "PROPUESTA" and del_asesor["actor_rol"] == "ASESOR"
    assert e.repo.contacto(del_asesor["id"])["correo"] == CONTACTO["correo"]
    assert "correo" not in del_asesor and "nombre_cifrado" not in del_asesor
    assert del_cliente["origen_contacto"] == "AUTH"


# ---------- código (W03-20)


def test_codigo_enmascarado_y_verificacion(e) -> None:
    propuesta = e.aceptar_como_asesor()

    enviado = e.servicio.enviar_codigo(propuesta["id"])
    malo = _motivo(e.servicio.verificar_codigo, propuesta["id"], "000000x")
    verificada = e.servicio.verificar_codigo(propuesta["id"], e.codigo_enviado())

    assert enviado["correo_enmascarado"] == "c•••@correo.com"
    assert enviado["vence_en"] == e.reloj.ahora + timedelta(minutes=10)
    solicitud = e.notificador.solicitudes[-1]
    assert solicitud.plantilla == "codigo-verificacion" and solicitud.correo == CONTACTO["correo"]
    assert re.fullmatch(r"\d{6}", solicitud.datos["codigo"])
    assert (malo.motivo, malo.detalle["intentos_restantes"]) == ("CODIGO_INVALIDO", 4)
    assert verificada["estado"] == "IDENTIDAD_VERIFICADA"


def test_el_asesor_corrige_el_correo_al_pedir_el_codigo(e) -> None:
    propuesta = e.aceptar_como_asesor()

    enviado = e.servicio.enviar_codigo(propuesta["id"], " Otra.Persona@Correo.com ")

    assert enviado["correo_enmascarado"] == "o•••@correo.com"
    assert e.repo.contacto(propuesta["id"])["correo"] == "otra.persona@correo.com"
    assert _motivo(e.servicio.enviar_codigo, propuesta["id"], "no-es-correo").motivo == (
        "DATOS_INVALIDOS"
    )


def test_codigo_vencido_intentos_agotados_y_pedir_otro_anula_el_anterior(e) -> None:
    propuesta = e.aceptar_como_asesor()
    e.servicio.enviar_codigo(propuesta["id"])
    viejo = e.codigo_enviado()
    e.servicio.enviar_codigo(propuesta["id"])
    nuevo = e.codigo_enviado()

    if viejo != nuevo:
        assert _motivo(e.servicio.verificar_codigo, propuesta["id"], viejo).motivo == (
            "CODIGO_INVALIDO"
        )
    e.reloj.ahora += timedelta(minutes=11)
    assert _motivo(e.servicio.verificar_codigo, propuesta["id"], nuevo).motivo == "CODIGO_VENCIDO"

    e.servicio.enviar_codigo(propuesta["id"])
    for _ in range(4):
        _motivo(e.servicio.verificar_codigo, propuesta["id"], "xxxxxx")
    agotado = _motivo(e.servicio.verificar_codigo, propuesta["id"], "xxxxxx")
    assert (agotado.motivo, agotado.detalle["intentos_restantes"]) == ("INTENTOS_AGOTADOS", 0)
    correcto_tarde = _motivo(e.servicio.verificar_codigo, propuesta["id"], e.codigo_enviado())
    assert correcto_tarde.motivo == "INTENTOS_AGOTADOS"


def test_un_cliente_con_cuenta_no_usa_codigo(e) -> None:
    propuesta = e.aceptar_como_cliente()

    assert _motivo(e.servicio.enviar_codigo, propuesta["id"]).motivo == "PROPUESTA_NO_VALIDA"


# ---------- «Emitir» del ASESOR (W03-21, W03-22)


def test_emitir_sin_verificar_no_se_puede(e) -> None:
    propuesta = e.aceptar_como_asesor()

    assert _motivo(e.servicio.emitir, propuesta["id"], "ASESOR").motivo == "PROPUESTA_NO_VALIDA"
    assert e.under.llamadas == []


def test_emitir_aprobado_envia_el_enlace_de_30_min_sin_devolver_la_url(e) -> None:
    propuesta, token = e.hasta_en_firma()

    solicitud = e.notificador.solicitudes[-1]
    assert solicitud.plantilla == "enlace-firma"
    assert solicitud.datos["enlace"].startswith("http://front/firma#t=")
    assert e.repo.obtener(propuesta["id"])["estado"] == "EN_FIRMA"
    assert e.repo.enlaces[-1]["vence_en"] == e.reloj.ahora + timedelta(minutes=30)
    assert token not in str(e.repo.enlaces)  # solo la huella
    resuelto = e.servicio.resolver_enlace(token)
    assert resuelto == {
        "propuesta_id": propuesta["id"],
        "vigente": True,
        "vence_en": e.repo.enlaces[-1]["vence_en"],
    }


@pytest.mark.parametrize(
    ("decision", "estado"), [("RECHAZADA", "NO_EMITIDA"), ("REVISION_MANUAL", "EN_REVISION")]
)
def test_si_under_no_aprueba_no_hay_enlace(decision, estado) -> None:
    e = Entorno(UnderFalso(decision, ["KYC"]))
    propuesta = e.aceptar_como_asesor()
    e.servicio.enviar_codigo(propuesta["id"])
    e.servicio.verificar_codigo(propuesta["id"], e.codigo_enviado())
    enviados = len(e.notificador.solicitudes)

    resultado = e.servicio.emitir(propuesta["id"], "ASESOR")

    assert resultado["propuesta"]["estado"] == estado and resultado["enlace"] is None
    assert len(e.notificador.solicitudes) == enviados


def test_under_caido_se_puede_reintentar(e) -> None:
    e.under.cae = True
    propuesta = e.aceptar_como_asesor()
    e.servicio.enviar_codigo(propuesta["id"])
    e.servicio.verificar_codigo(propuesta["id"], e.codigo_enviado())

    error = _motivo(e.servicio.emitir, propuesta["id"], "ASESOR")

    assert (error.motivo, error.estado_http) == ("SERVICIO_NO_DISPONIBLE", 503)
    assert e.repo.obtener(propuesta["id"])["estado"] == "IDENTIDAD_VERIFICADA"


def test_reenviar_el_enlace_anula_el_anterior(e) -> None:
    propuesta, viejo = e.hasta_en_firma()

    e.servicio.reenviar_enlace(propuesta["id"])
    nuevo = e.token_enviado()

    assert e.servicio.resolver_enlace(viejo)["vigente"] is False
    error = _motivo(e.servicio.documento, propuesta["id"], Autorizacion(enlace_token=viejo))
    assert error.motivo == "ENLACE_VENCIDO"
    assert e.servicio.documento(propuesta["id"], Autorizacion(enlace_token=nuevo))


# ---------- autorización doble (W03-33)


def test_sin_token_ni_titular_o_con_otro_la_propuesta_no_existe(e) -> None:
    propuesta, token = e.hasta_en_firma()
    otra = e.aceptar_como_asesor("oferta-2")

    for auth in (
        Autorizacion(),
        Autorizacion(enlace_token="token-que-no-existe-0000"),
        Autorizacion(titular_id="otro-titular"),
    ):
        assert _motivo(e.servicio.documento, propuesta["id"], auth).motivo == (
            "PROPUESTA_NO_ENCONTRADA"
        )
    # El token de una propuesta no sirve para otra.
    error = _motivo(e.servicio.documento, otra["id"], Autorizacion(enlace_token=token))
    assert error.motivo == "PROPUESTA_NO_ENCONTRADA"


def test_el_enlace_vence_a_los_30_minutos(e) -> None:
    propuesta, token = e.hasta_en_firma()
    e.reloj.ahora += timedelta(minutes=31)

    error = _motivo(e.servicio.documento, propuesta["id"], Autorizacion(enlace_token=token))
    estado = e.servicio.estado_para_enlace(propuesta["id"], Autorizacion(enlace_token=token))

    assert error.motivo == "ENLACE_VENCIDO"
    assert estado["vigente"] is False  # la página muestra el estado vacío, no un error


# ---------- flujo del CLIENTE con sesión (W03-32, W03-33)


def test_el_cliente_firma_en_su_sesion_sin_codigo_ni_enlace(e) -> None:
    propuesta = e.aceptar_como_cliente()
    auth = Autorizacion(titular_id=TITULAR)

    ajeno = _motivo(e.servicio.emitir, propuesta["id"], "CLIENTE", "otro-titular")
    resultado = e.servicio.emitir(propuesta["id"], "CLIENTE", TITULAR)
    evidencia, estado = e.firmar_nombre(propuesta["id"], auth)

    assert ajeno.motivo == "PROPUESTA_NO_ENCONTRADA"
    assert resultado["propuesta"]["estado"] == "EN_FIRMA" and resultado["enlace"] is None
    assert e.notificador.solicitudes == []  # ni código ni enlace por correo
    assert estado == "FIRMADA" and evidencia["modo_firma"] == "NOMBRE"
    assert evidencia["firmante_id"] == TITULAR


# ---------- firma en tres modos (W03-24)


def test_firma_con_nombre_estilizado(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)

    evidencia, estado = e.firmar_nombre(propuesta["id"], auth)
    otra_vez, _ = e.firmar_nombre(propuesta["id"], auth)

    assert estado == "FIRMADA" and otra_vez["id"] == evidencia["id"]  # idempotente
    assert e.repo.contacto(propuesta["id"])["firma_texto"] == "Camila"
    assert len(e.acl.solicitudes) == 1 and e.acl.solicitudes[0].firmante_id == TITULAR


def test_firma_con_imagen_valida_por_el_almacenamiento(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)

    subida = e.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 30_000)
    archivo_id = subida.archivo["id"]
    e.almacen.subir(archivo_id, png())
    evidencia, estado = e.servicio.firmar(
        propuesta["id"], auth, "v1", True, True, "TRAZO", archivo_id
    )

    assert subida.archivo["propietario_id"] == propuesta["id"]
    assert subida.archivo["consentimiento_id"] == "consentimiento-1"
    assert estado == "FIRMADA"
    assert (evidencia["modo_firma"], evidencia["imagen_firma_archivo_id"]) == ("TRAZO", archivo_id)


@pytest.mark.parametrize(
    ("contenido", "rechazar", "codigo"),
    [
        (png(1600, 300), False, "DIMENSIONES"),
        (png(fondo=(30, 120, 200, 255)), False, "FONDO"),
        (b"\x89PNG\r\n\x1a\nno-es-imagen", False, "ILEGIBLE"),
        (png(), True, "CONTENIDO_NO_COINCIDE"),
    ],
)
def test_una_imagen_invalida_se_rechaza_y_se_puede_subir_otra(e, contenido, rechazar, codigo):
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    archivo_id = e.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 30_000).archivo[
        "id"
    ]
    e.almacen.subir(archivo_id, contenido, rechazar=rechazar)

    error = _motivo(
        e.servicio.firmar, propuesta["id"], auth, "v1", True, True, "IMAGEN", archivo_id
    )

    assert (error.motivo, error.detalle["codigo"]) == ("IMAGEN_INVALIDA", codigo)
    assert e.repo.obtener(propuesta["id"])["estado"] == "EN_FIRMA"
    if not rechazar:
        assert archivo_id in e.almacen.retenidos  # la que rechaza POLICY se marca para borrar
    otra = e.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 30_000).archivo["id"]
    e.almacen.subir(otra, png())
    assert e.servicio.firmar(propuesta["id"], auth, "v1", True, True, "IMAGEN", otra)[1] == (
        "FIRMADA"
    )


def test_la_imagen_de_otra_propuesta_no_sirve(e) -> None:
    propuesta, token = e.hasta_en_firma()
    ajeno = e.almacen.solicitar_subida("otra-propuesta", None, "image/png", 100).archivo["id"]
    e.almacen.subir(ajeno, png())

    error = _motivo(
        e.servicio.firmar,
        propuesta["id"],
        Autorizacion(enlace_token=token),
        "v1",
        True,
        True,
        "IMAGEN",
        ajeno,
    )

    assert error.motivo == "PROPUESTA_NO_ENCONTRADA"


def test_firmar_exige_terminos_cobro_version_y_estado(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    firmar = e.servicio.firmar

    assert _motivo(
        firmar, propuesta["id"], auth, "v1", False, True, "NOMBRE", None, "Ca"
    ).motivo == ("COBRO_NO_AUTORIZADO")
    assert _motivo(
        firmar, propuesta["id"], auth, "v0", True, True, "NOMBRE", None, "Ca"
    ).motivo == ("VERSION_DOCUMENTO_DESACTUALIZADA")
    assert _motivo(firmar, propuesta["id"], auth, "v1", True, True, "NOMBRE", None, "C").motivo == (
        "DATOS_INVALIDOS"
    )
    assert _motivo(firmar, propuesta["id"], auth, "v1", True, True, "IMAGEN").motivo == (
        "DATOS_INVALIDOS"
    )


# ---------- pago y emisión (W03-28 a W03-30)


def test_pago_aprobado_emite_en_la_misma_respuesta_y_avisa(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    e.firmar_nombre(propuesta["id"], auth)

    pago = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/pago/retorno")
    sesion = pago["sesion"]
    estado_al_abrir = sesion.estado
    e.pagos.pagar(sesion.sesion_id)
    confirmado = e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)
    otra_vez = e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)

    assert estado_al_abrir == "abierta" and sesion.url_pago.endswith(sesion.sesion_id)
    poliza = confirmado["poliza"]
    assert poliza["numero_formateado"].startswith("SOL-")
    assert e.repo.obtener(propuesta["id"])["estado"] == "EMITIDA"
    assert otra_vez["poliza"]["id"] == poliza["id"]  # no emite dos veces
    assert e.planificador.encolados == [(propuesta["id"], 0)]  # el correo va por una tarea
    canales, mensaje = e.avisos.publicados[-1]
    assert canales == [
        "avisos:asesor:asesor-1",
        f"avisos:titular:{TITULAR}",
        "avisos:todos",
    ]
    assert '"estado": "EMITIDA"' in mensaje and CONTACTO["correo"] not in mensaje
    assert "Camila" not in mensaje  # sin datos personales


def test_un_rechazo_deja_la_firma_y_permite_otra_tarjeta(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    e.firmar_nombre(propuesta["id"], auth)
    sesion = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
    e.pagos.pagar(sesion.sesion_id, "rechazada", "fondos_insuficientes")

    rechazado = e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)
    otra = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]

    assert rechazado["sesion"].motivo == "fondos_insuficientes" and rechazado["poliza"] is None
    assert e.repo.obtener(propuesta["id"])["estado"] == "FIRMADA"
    assert e.repo.obtener(propuesta["id"])["motivo_estado"] == "PAGO_RECHAZADO"
    assert otra.sesion_id != sesion.sesion_id


def test_la_confirmacion_se_acepta_con_el_enlace_vencido(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    e.firmar_nombre(propuesta["id"], auth)
    sesion = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
    e.pagos.pagar(sesion.sesion_id)
    e.reloj.ahora += timedelta(minutes=40)

    assert _motivo(e.servicio.iniciar_pago, propuesta["id"], auth, "http://front/r").motivo == (
        "ENLACE_VENCIDO"
    )
    assert e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)["poliza"]


def test_payments_caido_nunca_da_un_5xx_crudo(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    e.firmar_nombre(propuesta["id"], auth)
    e.pagos.cae = True

    error = _motivo(e.servicio.iniciar_pago, propuesta["id"], auth, "http://front/r")

    assert (error.motivo, error.estado_http) == ("SERVICIO_NO_DISPONIBLE", 503)


def test_pagar_antes_de_firmar_no_se_puede(e) -> None:
    propuesta, token = e.hasta_en_firma()

    error = _motivo(
        e.servicio.iniciar_pago, propuesta["id"], Autorizacion(enlace_token=token), "http://f"
    )

    assert error.motivo == "PROPUESTA_NO_VALIDA" and e.pagos.aperturas == 0


def test_el_aviso_usa_el_contacto_de_la_propuesta_y_avisa_al_asesor(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    e.firmar_nombre(propuesta["id"], auth)
    sesion = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
    e.pagos.pagar(sesion.sesion_id)
    e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)

    e.emision.emitir(propuesta["id"])  # lo que corre la tarea

    plantillas = {s.plantilla: s for s in e.notificador.solicitudes}
    assert plantillas["poliza-emitida"].correo == CONTACTO["correo"]
    assert plantillas["poliza-emitida"].adjuntos[0].tipo == "application/pdf"
    assert plantillas["poliza-emitida-asesor"].destinatario_id == "asesor-1"
    assert plantillas["poliza-emitida-asesor"].correo is None  # sin datos del titular


# ---------- purga (W03-26)


def test_revocar_cancela_borra_el_contacto_y_retiene_la_imagen(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    archivo = e.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 100).archivo["id"]
    e.almacen.subir(archivo, png())
    e.servicio.firmar(propuesta["id"], auth, "v1", True, True, "IMAGEN", archivo)
    purga = ServicioPurga(e.repo, ConsentimientosFalso(), e.almacen, e.avisos)

    tocadas = purga.consentimiento_revocado(TITULAR, "consentimiento-1")
    otra_vez = purga.consentimiento_revocado(TITULAR, "consentimiento-1")

    assert tocadas == otra_vez == 1
    assert e.repo.obtener(propuesta["id"])["estado"] == "CANCELADA"
    assert e.repo.contacto(propuesta["id"]) == {"nombre": None, "correo": None, "firma_texto": None}
    assert archivo in e.almacen.retenidos
    assert _motivo(e.servicio.documento, propuesta["id"], auth).motivo == "ENLACE_VENCIDO"


def test_una_poliza_emitida_conserva_el_contacto(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    e.firmar_nombre(propuesta["id"], auth)
    sesion = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
    e.pagos.pagar(sesion.sesion_id)
    e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)

    ServicioPurga(e.repo, ConsentimientosFalso(), e.almacen).consentimiento_revocado(
        TITULAR, "consentimiento-1"
    )

    assert e.repo.contacto(propuesta["id"])["correo"] == CONTACTO["correo"]


def test_la_purga_diaria_pregunta_a_auth_y_no_borra_a_ciegas(e) -> None:
    vencida = e.aceptar_como_asesor()
    vigente = e.propuestas.aceptar("oferta-2", "asesor-1", "ASESOR", dict(CONTACTO))
    e.repo._propuesta(vigente["id"])["consentimiento_id"] = "consentimiento-2"

    caida = ServicioPurga(e.repo, ConsentimientosFalso(cae=True), e.almacen).purgar_vencidos()
    resumen = ServicioPurga(
        e.repo, ConsentimientosFalso({"consentimiento-1": False}), e.almacen
    ).purgar_vencidos()

    assert caida == {"revisadas": 2, "purgadas": 0, "sin_respuesta": 2}
    assert resumen == {"revisadas": 2, "purgadas": 1, "sin_respuesta": 0}
    assert e.repo.obtener(vencida["id"])["estado"] == "CANCELADA"
    assert e.repo.obtener(vigente["id"])["estado"] == "ACEPTADA"


# ---------- listas por rol (W03-35)


def test_listas_por_titular_por_asesor_y_por_estado_visible(e) -> None:
    del_asesor = e.aceptar_como_asesor()
    e.propuestas.aceptar("oferta-2", "otro-asesor", "ASESOR", dict(CONTACTO))

    del_titular, total_titular = e.propuestas.listar(TITULAR, None, None, 1, 10)
    del_asesor_1, total_asesor = e.propuestas.listar(None, "asesor-1", None, 1, 10)
    emitidas, total_emitidas = e.propuestas.listar(None, None, "EMITIDA", 1, 10)

    assert total_titular == 2  # el titular ve las dos, sin importar quién las vendió
    assert total_asesor == 1 and del_asesor_1[0]["id"] == del_asesor["id"]
    assert del_asesor_1[0]["nombre_titular"] == "Camila Rojas"
    assert total_emitidas == 0 and emitidas == []


# ---------- piezas sueltas


def test_validar_la_imagen() -> None:
    assert validar(png()) == (600, 200)
    assert validar(png(fondo=(0, 0, 0, 0))) == (600, 200)  # transparente
    for contenido, motivo in (
        (png(50, 20), "DIMENSIONES"),
        (png(1300, 300), "DIMENSIONES"),
        (png(fondo=(200, 0, 0, 255)), "FONDO"),
        (b"no es una imagen", "ILEGIBLE"),
    ):
        with pytest.raises(ImagenInvalida) as error:
            validar(contenido)
        assert error.value.motivo == motivo


def test_enmascarar_el_correo() -> None:
    assert enmascarar_correo("camila.rojas@correo.com") == "c•••@correo.com"


def test_el_pdf_estampa_la_imagen_o_el_nombre_y_el_borrador_no_lleva_datos_personales(e) -> None:
    propuesta, token = e.hasta_en_firma()
    poliza = {
        "id": "p",
        "cliente_id": TITULAR,
        "estado": "EMITIDA",
        "vigencia_desde": e.reloj.ahora,
        "vigencia_hasta": e.reloj.ahora,
    }
    evidencia = {
        "referencia_proveedor": "fe-1",
        "firmada_en": e.reloj.ahora,
        "documento_hash": "a" * 64,
    }
    datos = e.repo.obtener(propuesta["id"])

    con_imagen = generar_pdf(poliza, datos, evidencia, "SOL-1", firma_imagen=png())
    con_nombre = generar_pdf(poliza, datos, evidencia, "SOL-1", firma_texto="Camila Rojas")
    borrador = generar_borrador(datos)

    assert b"/Subtype /Image" in con_imagen
    assert b"Camila Rojas" in con_nombre
    assert b"Camila" not in borrador and borrador.startswith(b"%PDF")


def test_el_documento_baja_la_imagen_de_la_firma(e) -> None:
    propuesta, token = e.hasta_en_firma()
    auth = Autorizacion(enlace_token=token)
    archivo = e.servicio.solicitar_subida(propuesta["id"], auth, "image/png", 100).archivo["id"]
    e.almacen.subir(archivo, png())
    e.servicio.firmar(propuesta["id"], auth, "v1", True, True, "IMAGEN", archivo)
    sesion = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
    e.pagos.pagar(sesion.sesion_id)
    poliza = e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)["poliza"]

    pdf = ServicioPolizas(e.repo, almacenamiento=e.almacen).documento(poliza["id"])

    assert b"/Subtype /Image" in pdf


def test_nada_personal_en_los_logs(e, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    propuesta, token = e.hasta_en_firma()
    e.firmar_nombre(propuesta["id"], Autorizacion(enlace_token=token))

    for dato in (CONTACTO["correo"], "Camila", token, e.codigo_enviado()):
        assert dato not in caplog.text


# ---------- HTTP


def _http(e: Entorno) -> TestClient:
    app = create_app(
        config=Configuracion.desde_entorno(),
        servicio_propuestas=e.propuestas,
        servicio_emision=e.emision,
        servicio_polizas=ServicioPolizas(e.repo, almacenamiento=e.almacen),
        servicio_firma_y_pago=e.servicio,
        servicio_purga=ServicioPurga(e.repo, ConsentimientosFalso(), e.almacen),
    )
    return TestClient(app)


def test_http_flujo_del_asesor_con_cabeceras(e) -> None:
    with _http(e) as client:
        creada = client.post(
            "/propuestas",
            json={
                "oferta_id": "oferta-1",
                "actor_usuario_id": "asesor-1",
                "actor_rol": "ASESOR",
                "contacto": CONTACTO,
            },
        ).json()
        pid = creada["id"]
        client.post(f"/propuestas/{pid}/codigo", json={})
        malo = client.post(f"/propuestas/{pid}/codigo/verificar", json={"codigo": "x"})
        client.post(f"/propuestas/{pid}/codigo/verificar", json={"codigo": e.codigo_enviado()})
        emitida = client.post(f"/propuestas/{pid}/emitir", json={"rol": "ASESOR"}).json()
        token = e.token_enviado()
        resuelto = client.post("/enlaces/resolver", json={"token": token}).json()
        sin_auth = client.get(f"/propuestas/{pid}/contrato")
        contrato = client.get(f"/propuestas/{pid}/contrato", headers={"X-Enlace-Firma": token})
        pdf = client.get(f"/propuestas/{pid}/contrato.pdf", headers={"X-Enlace-Firma": token})
        estado = client.get(
            f"/propuestas/{pid}/enlace-estado", headers={"X-Enlace-Firma": token}
        ).json()
        lista = client.get("/propuestas", params={"actor_usuario_id": "asesor-1"}).json()

    assert creada["estado_visible"] == "EMITIENDO" and creada["origen_contacto"] == "PROPUESTA"
    assert malo.status_code == 422 and malo.json()["intentos_restantes"] == 4
    assert emitida["propuesta"]["estado"] == "EN_FIRMA"
    assert emitida["enlace"]["correo_enmascarado"] == "c•••@correo.com"
    assert token not in str(emitida)
    assert resuelto["propuesta_id"] == pid and resuelto["vigente"] is True
    assert sin_auth.status_code == 404
    assert contrato.status_code == 200 and contrato.json()["version"] == "v1"
    assert pdf.headers["content-type"] == "application/pdf"
    assert estado["vigente"] is True and estado["firma"] is None
    assert lista["total"] == 1 and lista["propuestas"][0]["nombre_titular"] == "Camila Rojas"


def test_http_titular_firma_paga_y_revoca(e) -> None:
    with _http(e) as client:
        pid = client.post(
            "/propuestas",
            json={"oferta_id": "oferta-1", "actor_usuario_id": "u-1", "actor_rol": "CLIENTE"},
        ).json()["id"]
        client.post(f"/propuestas/{pid}/emitir", json={"rol": "CLIENTE", "titular_id": TITULAR})
        cab = {"X-Titular-Id": TITULAR}
        firma = client.post(
            f"/propuestas/{pid}/firma-enlace",
            headers=cab,
            json={
                "version_documento": "v1",
                "acepta_terminos": True,
                "autoriza_cobro": True,
                "modo": "NOMBRE",
                "nombre_firma": "Camila Rojas",
            },
        )
        ajeno = client.post(
            f"/propuestas/{pid}/pago",
            headers={"X-Titular-Id": "otro"},
            json={"url_retorno": "http://front/r"},
        )
        pago = client.post(
            f"/propuestas/{pid}/pago", headers=cab, json={"url_retorno": "http://front/r"}
        ).json()
        sesion_id = pago["sesion"]["sesion_id"]
        e.pagos.pagar(sesion_id)
        confirmado = client.post(f"/propuestas/{pid}/pago/{sesion_id}/confirmar", headers=cab)
        revocado = client.post(
            "/propuestas/consentimiento-revocado",
            json={"titular_id": TITULAR, "consentimiento_id": "consentimiento-1"},
        )

    assert firma.status_code == 200 and firma.json()["estado"] == "FIRMADA"
    assert ajeno.status_code == 404
    assert pago["sesion"]["estado"] == "abierta"
    assert confirmado.status_code == 200
    assert confirmado.json()["poliza"]["numero"].startswith("SOL-")
    assert revocado.status_code == 204
