"""El resultado del onboarding llega a la cuenta de AUTH (HU-M01, contrato v0.20)."""

from datetime import timedelta

import pytest
from fakes import AHORA, APROBADA, Pila

from app.application.servicio_onboarding import COLUMNAS_PIEZAS, ErrorOnboarding


def _ligada(pila: Pila, usuario_id: str = "u-1") -> dict:
    sesion, _ = pila.sesion_lista()
    return pila.servicio.vincular_cuenta(sesion, usuario_id)


def test_verificada_activa_la_cuenta_con_nombre_y_documento() -> None:
    pila = Pila()
    sesion = _ligada(pila)
    pila.servicio.enviar(sesion)
    cerrada = pila.servicio.verificar(sesion["id"])
    assert pila.cuentas.llamadas == [("u-1", True, "ANA MARIA PRUEBA FICTICIA", "1000000001")]
    assert cerrada["cuenta_notificada_en"] is not None
    # Consultar otra vez no vuelve a llamar a AUTH.
    pila.servicio.de_usuario("u-1")
    assert len(pila.cuentas.llamadas) == 1


def test_cuenta_ligada_despues_del_cierre_se_notifica_al_ligar() -> None:
    pila = Pila()
    sesion = pila.verificada()
    assert pila.cuentas.llamadas == []
    pila.servicio.vincular_cuenta(sesion, "u-1")
    assert pila.cuentas.llamadas[0][:2] == ("u-1", True)


def test_rechazo_con_intentos_no_cierra_la_cuenta() -> None:
    pila = Pila({"estado": "rechazada", "motivo": "rostro_no_coincide"})
    sesion = _ligada(pila)
    pila.servicio.enviar(sesion)
    pila.servicio.verificar(sesion["id"])
    assert pila.cuentas.llamadas == []


def test_rechazo_sin_intentos_rechaza_la_cuenta_sin_datos() -> None:
    pila = Pila({"estado": "rechazada", "motivo": "rostro_no_coincide"})
    sesion = _ligada(pila)
    pila.repo.sesiones[sesion["id"]]["intento"] = 3
    pila.servicio.enviar(pila.repo.obtener(sesion["id"]))
    pila.servicio.verificar(sesion["id"])
    assert pila.cuentas.llamadas == [("u-1", False, None, None)]


def test_auth_caido_se_reintenta_al_consultar() -> None:
    pila = Pila()
    sesion = _ligada(pila)
    pila.cuentas.caido = True
    pila.servicio.enviar(sesion)
    cerrada = pila.servicio.verificar(sesion["id"])
    assert cerrada["estado"] == "VERIFICADA" and cerrada["cuenta_notificada_en"] is None
    pila.cuentas.caido = False
    assert pila.servicio.de_usuario("u-1")["cuenta_notificada_en"] is not None
    assert len(pila.cuentas.llamadas) == 1


def test_documento_de_otra_cuenta_rechaza_la_sesion_y_la_cuenta() -> None:
    pila = Pila()
    pila.cuentas.respuesta = "duplicado"
    sesion = _ligada(pila)
    pila.servicio.enviar(sesion)
    cerrada = pila.servicio.verificar(sesion["id"])
    assert (cerrada["estado"], cerrada["motivo"]) == ("RECHAZADA", "DOCUMENTO_YA_REGISTRADO")
    assert pila.servicio.vista(cerrada)["intentos_restantes"] == 0
    assert [ll[1] for ll in pila.cuentas.llamadas] == [True, False]
    assert pila.bus.eventos[-1]["tipo"] == "onboarding.rechazado"


def test_cuenta_que_ya_no_espera_se_da_por_notificada() -> None:
    pila = Pila()
    pila.cuentas.respuesta = "no_pendiente"
    sesion = _ligada(pila)
    pila.servicio.enviar(sesion)
    assert pila.servicio.verificar(sesion["id"])["cuenta_notificada_en"] is not None


def test_t20_la_cuenta_se_cierra_con_el_tipo_y_el_pais_de_la_sesion() -> None:
    from fakes import CLAVE_HMAC

    from app.application.identificadores import titular_id_desde_documento

    pila = Pila()
    sesion, _ = pila.sesion_lista("CE")
    sesion = pila.servicio.vincular_cuenta(sesion, "u-1")
    pila.servicio.enviar(sesion)
    cerrada = pila.servicio.verificar(sesion["id"])

    assert pila.cuentas.tipos == [("CE", "CO")]
    assert cerrada["titular_id"] == titular_id_desde_documento("1000000001", CLAVE_HMAC, "CO", "CE")
    assert cerrada["titular_id"] != titular_id_desde_documento("1000000001", CLAVE_HMAC)


def _rechazada_sin_intentos(pila: Pila, usuario_id: str = "u-1") -> dict:
    sesion = _ligada(pila, usuario_id)
    pila.repo.sesiones[sesion["id"]]["intento"] = 3
    pila.servicio.enviar(pila.repo.obtener(sesion["id"]))
    return pila.servicio.verificar(sesion["id"])


def test_kan31_tras_agotar_intentos_un_recorrido_nuevo_se_aprueba() -> None:
    """Agotar los 3 intentos no bloquea la cuenta: el recorrido nuevo llega a AUTH aprobado."""
    pila = Pila({"estado": "rechazada", "motivo": "rostro_no_coincide"})
    _rechazada_sin_intentos(pila)
    pila.identidad.respuesta = APROBADA
    nueva = _ligada(pila)
    pila.servicio.enviar(nueva)
    assert pila.servicio.verificar(nueva["id"])["estado"] == "VERIFICADA"
    assert [ll[1] for ll in pila.cuentas.llamadas] == [False, True]
    assert pila.servicio.de_usuario("u-1")["id"] == nueva["id"]


def test_kan31_tope_de_recorridos_rechazados_por_dia() -> None:
    pila = Pila({"estado": "rechazada", "motivo": "rostro_no_coincide"})
    for _ in range(3):
        _rechazada_sin_intentos(pila)
    with pytest.raises(ErrorOnboarding) as error:
        _ligada(pila)
    assert (error.value.codigo, error.value.estado_http) == ("RECORRIDOS_AGOTADOS", 429)
    # Otra cuenta no se ve afectada.
    assert _ligada(pila, "u-2")["usuario_id"] == "u-2"


def test_kan31_el_tope_se_libera_a_las_24_horas() -> None:
    pila = Pila({"estado": "rechazada", "motivo": "rostro_no_coincide"})
    pila.repo.ahora = AHORA - timedelta(hours=25)
    for _ in range(3):
        _rechazada_sin_intentos(pila)
    pila.repo.ahora = AHORA
    assert _ligada(pila)["usuario_id"] == "u-1"


def test_kan31_ligar_un_recorrido_nuevo_invalida_las_piezas_de_los_abiertos() -> None:
    """Aceptar el consentimiento de nuevo empieza limpio: el recorrido abierto anterior de la cuenta
    pierde sus piezas (empiezan a vencer) y el nuevo conserva las suyas. Otra cuenta no se toca."""
    pila = Pila(APROBADA)
    vieja = _ligada(pila)
    piezas_viejas = [vieja[c] for c in COLUMNAS_PIEZAS]
    ajena = _ligada(pila, "u-2")
    nueva = _ligada(pila)

    vieja_ahora = pila.repo.obtener(vieja["id"])
    assert all(vieja_ahora[c] is None for c in COLUMNAS_PIEZAS)
    assert vieja_ahora["estado"] == "INICIADO"
    assert set(piezas_viejas) <= set(pila.storage.retenidos)
    assert (vieja["id"], "INVALIDADA") in [(s, t) for s, t, _ in pila.repo.eventos]
    assert all(pila.repo.obtener(nueva["id"])[c] for c in COLUMNAS_PIEZAS)
    assert all(pila.repo.obtener(ajena["id"])[c] for c in COLUMNAS_PIEZAS)
    # La vieja ya no se puede enviar a verificar con fotos que no existen.
    with pytest.raises(ErrorOnboarding) as error:
        pila.servicio.enviar(vieja_ahora)
    assert error.value.codigo == "PIEZAS_INCOMPLETAS"
