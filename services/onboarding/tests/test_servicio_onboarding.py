"""Reglas del recorrido de M01 con dobles."""

import logging
from datetime import timedelta

import pytest
from fakes import AHORA, APROBADA, CLAVE_HMAC, Pila

from app.application.identificadores import titular_id_desde_documento
from app.application.planificador import VerificacionPendiente
from app.application.servicio_onboarding import ErrorOnboarding


def _codigo(error: pytest.ExceptionInfo) -> str:
    return error.value.codigo


def test_iniciar_guarda_solo_el_hash_del_token() -> None:
    pila = Pila()
    sesion, token = pila.servicio.iniciar("CO", "CC")
    assert token not in str(pila.repo.sesiones)
    assert pila.servicio.autenticar(sesion["id"], token)["id"] == sesion["id"]


@pytest.mark.parametrize("jurisdiccion,tipo", [("MX", "CC"), ("CO", "PASAPORTE")])
def test_iniciar_rechaza_jurisdiccion_o_documento_no_soportado(jurisdiccion, tipo) -> None:
    with pytest.raises(ErrorOnboarding) as error:
        Pila().servicio.iniciar(jurisdiccion, tipo)
    assert error.value.estado_http == 422


def test_token_ajeno_o_vencido_responde_el_mismo_401() -> None:
    pila = Pila()
    sesion, token = pila.servicio.iniciar("CO", "CC")
    for sesion_id, ficha in ((sesion["id"], "otro"), ("no-existe", token), (sesion["id"], None)):
        with pytest.raises(ErrorOnboarding) as error:
            pila.servicio.autenticar(sesion_id, ficha)
        assert _codigo(error) == "TOKEN_INVALIDO"
    pila.servicio._reloj = lambda: AHORA + timedelta(minutes=31)
    with pytest.raises(ErrorOnboarding):
        pila.servicio.autenticar(sesion["id"], token)


def test_sin_consentimiento_no_hay_subida() -> None:
    pila = Pila()
    sesion, _ = pila.servicio.iniciar("CO", "CC")
    with pytest.raises(ErrorOnboarding) as error:
        pila.servicio.solicitar_subida(sesion, "ANVERSO", "image/jpeg", 100)
    assert _codigo(error) == "SIN_CONSENTIMIENTO"


def test_la_subida_va_a_nombre_de_la_sesion_con_su_consentimiento() -> None:
    pila = Pila()
    sesion, _ = pila.sesion_lista()
    archivo = pila.storage.archivos[sesion["anverso_id"]]
    assert archivo["propietario_id"] == sesion["id"]
    assert archivo["consentimiento_id"] == "c-1"
    assert pila.storage.archivos[sesion["video_id"]]["tipo"] == "onboarding-video"


def test_el_rechazo_del_catalogo_llega_como_422() -> None:
    pila = Pila()
    sesion, _ = pila.servicio.iniciar("CO", "CC")
    sesion = pila.servicio.registrar_consentimiento(sesion, "c-1")
    with pytest.raises(ErrorOnboarding) as error:
        pila.servicio.solicitar_subida(sesion, "ROSTRO", "image/png", 100)
    assert (error.value.estado_http, _codigo(error)) == (422, "TIPO_CONTENIDO_NO_PERMITIDO")


def test_confirmar_un_archivo_ajeno_es_404() -> None:
    pila = Pila()
    sesion, _ = pila.sesion_lista()
    with pytest.raises(ErrorOnboarding) as error:
        pila.servicio.confirmar_subida(sesion, "a-999")
    assert _codigo(error) == "ARCHIVO_NO_ENCONTRADO"


def test_enviar_sin_las_cuatro_piezas_disponibles_es_409() -> None:
    pila = Pila()
    sesion, _ = pila.servicio.iniciar("CO", "CC")
    sesion = pila.servicio.registrar_consentimiento(sesion, "c-1")
    pila.servicio.solicitar_subida(sesion, "ANVERSO", "image/jpeg", 100)
    with pytest.raises(ErrorOnboarding) as error:
        pila.servicio.enviar(pila.repo.obtener(sesion["id"]))
    assert _codigo(error) == "PIEZAS_INCOMPLETAS"


def test_enviar_agenda_la_verificacion_y_es_idempotente() -> None:
    pila = Pila()
    sesion, _ = pila.sesion_lista()
    enviada = pila.servicio.enviar(sesion)
    assert enviada["estado"] == "EN_VERIFICACION"
    pila.servicio.enviar(enviada)
    assert pila.planificador.encoladas == [sesion["id"], sesion["id"]]


def test_aprobada_queda_verificada_con_titular_y_datos_cifrados() -> None:
    pila = Pila()
    sesion = pila.verificada()
    assert sesion["estado"] == "VERIFICADA"
    assert sesion["titular_id"] == titular_id_desde_documento("1000000001", CLAVE_HMAC)
    datos = pila.repo.datos[sesion["id"]]
    assert "1000000001" not in str(datos) and "ANA" not in str(datos)
    assert pila.cifrador.descifrar(datos["numero"], sesion["id"]) == "1000000001"
    assert [e["tipo"] for e in pila.bus.eventos] == ["onboarding.verificado"]
    assert len(pila.storage.retenidos) == 4
    # Al ACL van las URLs firmadas de anverso, rostro y reverso, con una referencia por intento.
    referencia, anverso, rostro, reverso = pila.identidad.llamadas[0]
    assert referencia == f"{sesion['id']}-1"
    assert (anverso, rostro, reverso) == (
        f"http://gcs/{sesion['anverso_id']}?firma=x",
        f"http://gcs/{sesion['rostro_id']}?firma=x",
        f"http://gcs/{sesion['reverso_id']}?firma=x",
    )


def test_verificar_dos_veces_no_repite_nada() -> None:
    pila = Pila()
    sesion = pila.verificada()
    assert pila.servicio.verificar(sesion["id"])["estado"] == "VERIFICADA"
    assert len(pila.identidad.llamadas) == 1
    assert len(pila.bus.eventos) == 1


@pytest.mark.parametrize(
    "motivo_acl,motivo",
    [
        ("documento_rechazado", "ILEGIBLE"),
        ("prueba_de_vida_fallida", "SUPLANTACION"),
        ("rostro_no_coincide", "ROSTRO_NO_COINCIDE"),
    ],
)
def test_rechazos_del_proveedor(motivo_acl, motivo) -> None:
    pila = Pila({"estado": "rechazada", "motivo": motivo_acl, "alertas": ["X"]})
    sesion = pila.verificada()
    assert (sesion["estado"], sesion["motivo"]) == ("RECHAZADA", motivo)
    assert pila.repo.datos == {}  # de un rechazo no se guarda nada leído del documento
    assert pila.bus.eventos[0]["datos"]["motivo"] == motivo


def test_menor_de_edad() -> None:
    respuesta = {**APROBADA, "datos": {**APROBADA["datos"], "fecha_nacimiento": "2010-01-01"}}
    sesion = Pila(respuesta).verificada()
    assert (sesion["estado"], sesion["motivo"]) == ("RECHAZADA", "MENOR_DE_EDAD")


def test_sin_numero_o_fecha_es_ilegible() -> None:
    respuesta = {**APROBADA, "datos": {**APROBADA["datos"], "numero": None}}
    assert Pila(respuesta).verificada()["motivo"] == "ILEGIBLE"


def test_un_documento_una_cuenta() -> None:
    pila = Pila()
    assert pila.verificada()["estado"] == "VERIFICADA"
    segunda = pila.verificada()
    assert (segunda["estado"], segunda["motivo"]) == ("RECHAZADA", "DOCUMENTO_YA_REGISTRADO")
    assert pila.servicio.vista(segunda)["intentos_restantes"] == 0
    with pytest.raises(ErrorOnboarding):
        pila.servicio.reintentar(segunda)


def test_carrera_en_el_cierre_tambien_es_documento_ya_registrado(monkeypatch) -> None:
    pila = Pila()
    pila.verificada()
    monkeypatch.setattr(pila.repo, "titular_verificado_en_otra", lambda *_: False)
    assert pila.verificada()["motivo"] == "DOCUMENTO_YA_REGISTRADO"


def test_proveedor_pendiente_queda_en_revision_sin_evento() -> None:
    sesion = Pila({"estado": "pendiente", "motivo": "circuito_abierto"}).verificada()
    assert (sesion["estado"], sesion["motivo"]) == ("EN_REVISION", "PROVEEDOR_NO_DISPONIBLE")


def test_acl_o_storage_caidos_dejan_la_tarea_pendiente() -> None:
    pila = Pila()
    sesion, _ = pila.sesion_lista()
    pila.servicio.enviar(sesion)
    pila.identidad.caido = True
    with pytest.raises(VerificacionPendiente):
        pila.servicio.verificar(sesion["id"])
    pila.identidad.caido = False
    pila.storage.caido = True
    with pytest.raises(VerificacionPendiente):
        pila.servicio.verificar(sesion["id"])
    assert pila.repo.obtener(sesion["id"])["estado"] == "EN_VERIFICACION"


def test_reintentos_hasta_tres() -> None:
    pila = Pila({"estado": "rechazada", "motivo": "rostro_no_coincide"})
    sesion = pila.verificada()
    for intento in (2, 3):
        sesion = pila.servicio.reintentar(sesion)
        assert (sesion["estado"], sesion["intento"], sesion["anverso_id"]) == (
            "INICIADO",
            intento,
            None,
        )
        # Se simula el nuevo rechazo sin volver a subir las piezas.
        pila.repo.sesiones[sesion["id"]]["estado"] = "RECHAZADA"
        sesion = pila.repo.obtener(sesion["id"])
    assert pila.servicio.vista(sesion)["intentos_restantes"] == 0
    with pytest.raises(ErrorOnboarding) as error:
        pila.servicio.reintentar(sesion)
    assert _codigo(error) == "SIN_INTENTOS"


def test_vincular_cuenta_una_sola_vez() -> None:
    pila = Pila()
    sesion, _ = pila.servicio.iniciar("CO", "CC")
    sesion = pila.servicio.vincular_cuenta(sesion, "u-1")
    assert pila.servicio.vincular_cuenta(sesion, "u-1")["usuario_id"] == "u-1"
    with pytest.raises(ErrorOnboarding):
        pila.servicio.vincular_cuenta(sesion, "u-2")
    assert pila.servicio.de_usuario("u-1")["id"] == sesion["id"]


def test_sin_datos_personales_en_logs_ni_eventos(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    pila = Pila()
    sesion = pila.verificada()
    texto = caplog.text + str(pila.bus.eventos) + str(pila.repo.eventos)
    texto += str(pila.repo.verificaciones)
    for dato in ("1000000001", "ANA", "PRUEBA", "1990-05-17", sesion["titular_id"]):
        assert dato not in texto
