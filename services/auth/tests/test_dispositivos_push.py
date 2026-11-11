"""Dispositivos de push (KAN-259): un solo dispositivo con push activo por cliente, token cifrado
y preferencia. No se relaciona con `tests/test_dispositivos.py` (login de confianza, KAN-26)."""

from datetime import UTC, datetime

import pytest
from fakes import CIFRADOR_PRUEBAS, RepositorioDispositivosPushMemoria

from app.dispositivos_push import (
    DatoInvalido,
    PushDeshabilitado,
    ServicioDispositivosPush,
    SinDispositivoActivo,
    hash_token,
)

USUARIO = "usuario-1"
AHORA = datetime(2026, 11, 10, tzinfo=UTC)


def _preparar(habilitado: bool = True):
    repo = RepositorioDispositivosPushMemoria()
    servicio = ServicioDispositivosPush(
        repo, CIFRADOR_PRUEBAS, habilitado_push=lambda _uid: habilitado
    )
    return servicio, repo


def test_registrar_sesion_deja_un_solo_dispositivo_activo() -> None:
    servicio, repo = _preparar()

    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)
    servicio.registrar_sesion(USUARIO, "instalacion-B", "android", "familia-2", AHORA)

    activos = [f for f in repo.filas.values() if f["activo"]]
    assert len(activos) == 1
    assert activos[0]["instalacion_id"] == "instalacion-B"
    inactivo = next(f for f in repo.filas.values() if f["instalacion_id"] == "instalacion-A")
    assert inactivo["motivo_desactivacion"] == "NUEVA_SESION"


def test_reinstalar_la_misma_instalacion_reactiva_la_fila() -> None:
    servicio, repo = _preparar()
    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)
    servicio.cerrar_sesion(USUARIO, "familia-1", AHORA)

    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-3", AHORA)

    assert len(repo.filas) == 1
    fila = next(iter(repo.filas.values()))
    assert fila["activo"] and fila["familia_id"] == "familia-3"


def test_datos_invalidos_de_instalacion_o_plataforma() -> None:
    servicio, _ = _preparar()
    with pytest.raises(DatoInvalido):
        servicio.registrar_sesion(USUARIO, "", "android", "familia-1", AHORA)
    with pytest.raises(DatoInvalido):
        servicio.registrar_sesion(USUARIO, "instalacion-A", "windows", "familia-1", AHORA)


def test_registrar_token_cifra_y_permite_leerlo_en_destino_push() -> None:
    servicio, _ = _preparar()
    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)

    servicio.registrar_token(USUARIO, "instalacion-A", "token-fcm-real", AHORA)
    destino = servicio.destino_push(USUARIO)

    assert destino["token"] == "token-fcm-real"
    assert destino["plataforma"] == "android"


def test_registrar_token_sin_dispositivo_activo_falla() -> None:
    servicio, _ = _preparar()
    with pytest.raises(DatoInvalido):
        servicio.registrar_token(USUARIO, "instalacion-fantasma", "token", AHORA)


def test_token_repetido_en_otro_usuario_desactiva_el_dispositivo_viejo() -> None:
    servicio, repo = _preparar()
    servicio.registrar_sesion("usuario-viejo", "tel-compartido", "android", "familia-1", AHORA)
    servicio.registrar_token("usuario-viejo", "tel-compartido", "token-compartido", AHORA)

    servicio.registrar_sesion(USUARIO, "tel-compartido-2", "android", "familia-2", AHORA)
    servicio.registrar_token(USUARIO, "tel-compartido-2", "token-compartido", AHORA)

    viejo = next(f for f in repo.filas.values() if f["usuario_id"] == "usuario-viejo")
    assert viejo["activo"] is False
    assert viejo["token_fcm_hash"] is None


def test_destino_push_sin_dispositivo_activo() -> None:
    servicio, _ = _preparar()
    with pytest.raises(SinDispositivoActivo):
        servicio.destino_push(USUARIO)


def test_destino_push_con_push_deshabilitado() -> None:
    servicio, _ = _preparar(habilitado=False)
    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)
    servicio.registrar_token(USUARIO, "instalacion-A", "token", AHORA)

    with pytest.raises(PushDeshabilitado):
        servicio.destino_push(USUARIO)


def test_cerrar_sesion_apaga_el_dispositivo_de_esa_familia() -> None:
    servicio, repo = _preparar()
    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)

    servicio.cerrar_sesion(USUARIO, "familia-1", AHORA)

    fila = next(iter(repo.filas.values()))
    assert fila["activo"] is False
    assert fila["motivo_desactivacion"] == "CIERRE_SESION"


def test_revocar_de_usuario_apaga_todos_sus_dispositivos() -> None:
    servicio, repo = _preparar()
    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)

    servicio.revocar_de_usuario(USUARIO, AHORA)

    fila = next(iter(repo.filas.values()))
    assert fila["activo"] is False
    assert fila["motivo_desactivacion"] == "CUENTA_SUSPENDIDA"


def test_token_invalido_borra_el_token_pero_no_desactiva_la_sesion() -> None:
    servicio, repo = _preparar()
    servicio.registrar_sesion(USUARIO, "instalacion-A", "android", "familia-1", AHORA)
    servicio.registrar_token(USUARIO, "instalacion-A", "token-fcm", AHORA)
    dispositivo_id = next(iter(repo.filas))

    servicio.token_invalido(dispositivo_id, AHORA)

    fila = repo.filas[dispositivo_id]
    assert fila["activo"] is True
    assert fila["token_fcm_cifrado"] is None
    with pytest.raises(SinDispositivoActivo):
        servicio.destino_push(USUARIO)


def test_hash_token_es_determinista() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abcd")
