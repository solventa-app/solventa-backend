"""D3 (KAN-259): una sola sesión activa por cliente, solo entre sesiones `app`, y su enganche con
`app/dispositivos_push.py`. No toca el login de confianza (KAN-26)."""

from fakes import (
    CIFRADOR_PRUEBAS,
    CLAVE_TITULAR_PRUEBAS,
    HASHER_RAPIDO,
    Reloj,
    RepositorioDispositivosPushMemoria,
    RepositorioRefrescosMemoria,
    RepositorioUsuariosMemoria,
    extras_cliente,
)

from app.dispositivos_push import ServicioDispositivosPush
from app.sesiones import CANAL_APP, ServicioSesiones
from app.usuarios import ServicioUsuarios

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE = "Clave-segura-1"
DISPOSITIVO_A = {"instalacion_id": "instalacion-A", "plataforma": "android"}
DISPOSITIVO_B = {"instalacion_id": "instalacion-B", "plataforma": "android"}


def _preparar(rol: str = "CLIENTE"):
    repositorio = RepositorioUsuariosMemoria()
    refrescos = RepositorioRefrescosMemoria()
    repo_push = RepositorioDispositivosPushMemoria()
    reloj = Reloj()
    dispositivos_push = ServicioDispositivosPush(
        repo_push, CIFRADOR_PRUEBAS, habilitado_push=lambda _uid: True
    )
    usuario = ServicioUsuarios(
        repositorio, HASHER_RAPIDO, CIFRADOR_PRUEBAS, CLAVE_TITULAR_PRUEBAS
    ).crear(
        "cliente.259", CLAVE, rol, **(extras_cliente("cliente.259") if rol == "CLIENTE" else {})
    )
    sesiones = ServicioSesiones(
        repositorio,
        HASHER_RAPIDO,
        SECRETO,
        60,
        reloj=reloj,
        refrescos=refrescos,
        dispositivos_push=dispositivos_push,
    )
    return sesiones, refrescos, repo_push, usuario


def test_entrar_en_un_segundo_telefono_revoca_el_primero_y_lo_devuelve_como_sid_revocado() -> None:
    sesiones, refrescos, repo_push, _ = _preparar()

    primera = sesiones.iniciar_sesion(
        "cliente.259", CLAVE, canal=CANAL_APP, dispositivo=DISPOSITIVO_A
    )
    primera_familia = _familia_de(refrescos, primera)

    segunda = sesiones.iniciar_sesion(
        "cliente.259", CLAVE, canal=CANAL_APP, dispositivo=DISPOSITIVO_B
    )

    # el sid (familia) de la primera sesión vuelve como revocado, para que el BFF lo anote
    # en `sesiones_revocadas` al instante
    assert segunda["sids_revocados"] == [primera_familia]
    # y su cadena de refresh queda revocada en la base
    assert all(
        f["revocado_en"] is not None
        for f in refrescos.filas.values()
        if f["familia_id"] == primera_familia
    )
    # y su dispositivo de push quedó apagado
    viejo = next(f for f in repo_push.filas.values() if f["instalacion_id"] == "instalacion-A")
    assert viejo["activo"] is False
    assert viejo["motivo_desactivacion"] == "NUEVA_SESION"
    nuevo = next(f for f in repo_push.filas.values() if f["instalacion_id"] == "instalacion-B")
    assert nuevo["activo"] is True


def _familia_de(refrescos: RepositorioRefrescosMemoria, sesion: dict) -> str:
    from app.refrescos import hash_refresh

    fila = refrescos.obtener_por_hash(hash_refresh(sesion["refresh_token"]))
    assert fila is not None
    return fila["familia_id"]


def test_sin_dispositivo_tambien_revoca_la_sesion_app_anterior() -> None:
    """D3 aplica aunque la app no mande el bloque `dispositivo` (versión vieja de la app): solo
    una sesión `app` queda viva, aunque no haya push que registrar."""
    sesiones, refrescos, _, _ = _preparar()

    primera = sesiones.iniciar_sesion("cliente.259", CLAVE, canal=CANAL_APP)
    sesiones.iniciar_sesion("cliente.259", CLAVE, canal=CANAL_APP)

    primera_familia = _familia_de(refrescos, primera)
    assert all(
        f["revocado_en"] is not None
        for f in refrescos.filas.values()
        if f["familia_id"] == primera_familia
    )


def test_la_sesion_web_no_se_ve_afectada_por_un_login_en_la_app() -> None:
    """D3: la regla es solo entre sesiones `app`; la web del cliente sigue viva."""
    sesiones, refrescos, _, _ = _preparar()

    web = sesiones.iniciar_sesion("cliente.259", CLAVE, canal="web")
    sesiones.iniciar_sesion("cliente.259", CLAVE, canal=CANAL_APP, dispositivo=DISPOSITIVO_A)

    familia_web = _familia_de(refrescos, web)
    assert all(
        f["revocado_en"] is None for f in refrescos.filas.values() if f["familia_id"] == familia_web
    )


def test_un_asesor_no_tiene_sesion_unica_de_la_app() -> None:
    """D3 solo aplica a CLIENTE: un ASESOR ni siquiera entra por el canal `app` (T17), así que dos
    sesiones suyas (en la web) nunca se revocan entre sí por esta regla."""
    sesiones, refrescos, _, _ = _preparar(rol="ASESOR")

    primera = sesiones.iniciar_sesion("cliente.259", CLAVE, canal="web")
    segunda = sesiones.iniciar_sesion("cliente.259", CLAVE, canal="web")

    assert "sids_revocados" not in primera
    assert "sids_revocados" not in segunda
    familia_primera = _familia_de(refrescos, primera)
    assert all(
        f["revocado_en"] is None
        for f in refrescos.filas.values()
        if f["familia_id"] == familia_primera
    )
