"""Regla de la clave en altas y rol en el login (T17)."""

import pytest
from fakes import HASHER_RAPIDO, Reloj, RepositorioUsuariosMemoria

from app.sesiones import CredencialesInvalidas, ServicioSesiones
from app.usuarios import DatoInvalido, ServicioUsuarios, validar_clave

SECRETO = "secreto-de-prueba-con-32-bytes-o-mas"
CLAVE = "Clave-segura-1"


@pytest.mark.parametrize(
    "clave",
    ["Contraseña¿1", "Ñandú€uno", "Größe-Ärger1", "Zażółć Gęślą", "Açaí_Brasil", "Abcdefg😀"],
)
def test_claves_validas(clave: str) -> None:
    validar_clave(clave)


@pytest.mark.parametrize(
    ("clave", "mensaje"),
    [
        ("clave1234", "La clave no es segura: falta una mayúscula y un carácter especial."),
        ("CLAVE1234!", "La clave no es segura: falta una minúscula."),
        ("Abcdefg1", "La clave no es segura: falta un carácter especial."),
        ("日本語のパスワード!", "La clave no es segura: falta una mayúscula y una minúscula."),
        ("corta", "La clave debe tener entre 8 y 128 caracteres."),
    ],
)
def test_claves_invalidas(clave: str, mensaje: str) -> None:
    with pytest.raises(DatoInvalido, match=mensaje):
        validar_clave(clave)


def test_crear_usuario_rechaza_la_clave_debil() -> None:
    servicio = ServicioUsuarios(RepositorioUsuariosMemoria(), HASHER_RAPIDO)
    with pytest.raises(DatoInvalido):
        servicio.crear("asesor.017", "clave1234", "ASESOR")


def _sesiones():
    repo = RepositorioUsuariosMemoria()
    usuarios = ServicioUsuarios(repo, HASHER_RAPIDO)
    usuarios.crear("asesor.017", CLAVE, "ASESOR")
    return ServicioSesiones(repo, HASHER_RAPIDO, SECRETO, 60, reloj=Reloj())


def test_login_con_el_rol_de_la_cuenta_entra() -> None:
    assert _sesiones().iniciar_sesion("asesor.017", CLAVE, "web", "ASESOR")["token"]


def test_login_con_otro_rol_falla_igual_que_una_clave_errada() -> None:
    sesiones = _sesiones()
    with pytest.raises(CredencialesInvalidas) as por_rol:
        sesiones.iniciar_sesion("asesor.017", CLAVE, "web", "BACKOFFICE")
    with pytest.raises(CredencialesInvalidas) as por_clave:
        sesiones.iniciar_sesion("asesor.017", "Otra-clave-9", "web", "ASESOR")
    assert str(por_rol.value) == str(por_clave.value)


def test_la_app_solo_admite_clientes() -> None:
    with pytest.raises(CredencialesInvalidas):
        _sesiones().iniciar_sesion("asesor.017", CLAVE, "app")
