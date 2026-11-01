"""Inicio de sesión: JWT, mensaje genérico, bloqueo por intentos y suspensión."""

import logging
from datetime import timedelta

import jwt
import pytest
from fakes import HASHER_RAPIDO, Reloj, RepositorioUsuariosMemoria

from app.sesiones import (
    MAX_INTENTOS,
    MENSAJE_CREDENCIALES,
    MINUTOS_BLOQUEO,
    CredencialesInvalidas,
    ServicioSesiones,
)
from app.usuarios import ServicioUsuarios

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE = "Clave-segura-1"


def _preparar(rol: str = "ASESOR"):
    repositorio = RepositorioUsuariosMemoria()
    reloj = Reloj()
    usuario = ServicioUsuarios(repositorio, HASHER_RAPIDO).crear("asesor.017", CLAVE, rol)
    sesiones = ServicioSesiones(repositorio, HASHER_RAPIDO, SECRETO, 60, reloj=reloj)
    return sesiones, repositorio, reloj, usuario


def test_login_correcto_emite_jwt_con_los_claims_esperados() -> None:
    sesiones, _, reloj, usuario = _preparar()

    sesion = sesiones.iniciar_sesion("asesor.017", CLAVE)

    claims = jwt.decode(
        sesion["token"], SECRETO, algorithms=["HS256"], options={"verify_exp": False}
    )
    assert claims["sub"] == usuario["id"]
    assert claims["rol"] == "ASESOR"
    assert claims["scopes"] == ["consentimiento", "oferta"]
    assert claims["exp"] - claims["iat"] == 60 * 60
    assert claims["jti"]
    assert sesion["expira_en"] == reloj.ahora + timedelta(minutes=60)
    assert "hash_clave" not in sesion["usuario"]


def test_el_token_no_valida_con_otro_secreto() -> None:
    sesiones, _, _, _ = _preparar()
    token = sesiones.iniciar_sesion("asesor.017", CLAVE)["token"]

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token,
            "otro-secreto-distinto-de-32-bytes-o-mas",
            algorithms=["HS256"],
            options={"verify_exp": False},
        )


def test_clave_erronea_y_usuario_inexistente_dan_el_mismo_mensaje() -> None:
    sesiones, _, _, _ = _preparar()

    with pytest.raises(CredencialesInvalidas) as clave_mala:
        sesiones.iniciar_sesion("asesor.017", "no-es-la-clave")
    with pytest.raises(CredencialesInvalidas) as no_existe:
        sesiones.iniciar_sesion("nadie.aqui", CLAVE)

    assert str(clave_mala.value) == str(no_existe.value) == MENSAJE_CREDENCIALES


def test_bloqueo_tras_los_intentos_fallidos_aun_con_la_clave_correcta() -> None:
    sesiones, repositorio, _, usuario = _preparar()

    for _ in range(MAX_INTENTOS):
        with pytest.raises(CredencialesInvalidas):
            sesiones.iniciar_sesion("asesor.017", "no-es-la-clave")

    assert repositorio.filas[usuario["id"]]["bloqueado_hasta"] is not None
    with pytest.raises(CredencialesInvalidas):
        sesiones.iniciar_sesion("asesor.017", CLAVE)


def test_el_bloqueo_vence_solo() -> None:
    sesiones, _, reloj, _ = _preparar()
    for _ in range(MAX_INTENTOS):
        with pytest.raises(CredencialesInvalidas):
            sesiones.iniciar_sesion("asesor.017", "no-es-la-clave")

    reloj.ahora += timedelta(minutes=MINUTOS_BLOQUEO, seconds=1)

    assert sesiones.iniciar_sesion("asesor.017", CLAVE)["token"]


def test_un_login_correcto_reinicia_el_contador() -> None:
    sesiones, repositorio, _, usuario = _preparar()
    with pytest.raises(CredencialesInvalidas):
        sesiones.iniciar_sesion("asesor.017", "no-es-la-clave")

    sesiones.iniciar_sesion("asesor.017", CLAVE)

    assert repositorio.filas[usuario["id"]]["intentos_fallidos"] == 0


def test_usuario_suspendido_no_inicia_sesion() -> None:
    sesiones, repositorio, _, usuario = _preparar()
    repositorio.suspender(usuario["id"])

    with pytest.raises(CredencialesInvalidas, match=MENSAJE_CREDENCIALES):
        sesiones.iniciar_sesion("asesor.017", CLAVE)


def test_los_logs_no_llevan_alias_clave_ni_token(caplog) -> None:
    sesiones, _, _, _ = _preparar()

    with caplog.at_level(logging.DEBUG):
        token = sesiones.iniciar_sesion("asesor.017", CLAVE)["token"]
        with pytest.raises(CredencialesInvalidas):
            sesiones.iniciar_sesion("asesor.017", "no-es-la-clave")

    assert caplog.records
    for prohibido in ("asesor.017", CLAVE, "no-es-la-clave", token):
        assert prohibido not in caplog.text
