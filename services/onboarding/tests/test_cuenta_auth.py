"""El resultado del onboarding llega a la cuenta de AUTH (HU-M01, contrato v0.20)."""

from fakes import Pila


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
