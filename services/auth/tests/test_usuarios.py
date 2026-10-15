"""Reglas de alta y administración de usuarios, con el repositorio en memoria."""

import pytest
from fakes import HASHER_RAPIDO, RepositorioUsuariosMemoria

from app.usuarios import DatoInvalido, ServicioUsuarios, UsuarioDuplicado


def _servicio() -> tuple[ServicioUsuarios, RepositorioUsuariosMemoria]:
    repositorio = RepositorioUsuariosMemoria()
    return ServicioUsuarios(repositorio, HASHER_RAPIDO), repositorio


def test_crear_devuelve_solo_datos_publicos() -> None:
    servicio, _ = _servicio()

    usuario = servicio.crear("asesor.017", "clave-segura-1", "ASESOR")

    assert set(usuario) == {"id", "usuario", "rol", "estado", "creado_en"}
    assert usuario["estado"] == "ACTIVO"


def test_la_clave_se_guarda_como_hash_argon2() -> None:
    servicio, repositorio = _servicio()

    usuario = servicio.crear("asesor.017", "clave-segura-1", "ASESOR")

    guardado = repositorio.filas[usuario["id"]]["hash_clave"]
    assert guardado.startswith("$argon2id$")
    assert "clave-segura-1" not in guardado


@pytest.mark.parametrize(
    "alias",
    ["ab", "Mayusculas", "con espacio", "persona@correo.com", "cc1020304050", "x" * 33],
)
def test_alias_invalido_se_rechaza(alias: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.crear(alias, "clave-segura-1", "ASESOR")


@pytest.mark.parametrize(
    "alias", ["persona@correo.com", "cliente.1020304050", "Persona@Correo.com"]
)
def test_alias_con_forma_de_correo_o_documento_tiene_mensaje_claro(alias: str) -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido, match="correo ni un número de documento"):
        servicio.crear(alias, "clave-segura-1", "CLIENTE")


def test_clave_corta_se_rechaza() -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.crear("asesor.017", "corta", "ASESOR")


def test_rol_desconocido_se_rechaza() -> None:
    servicio, _ = _servicio()

    with pytest.raises(DatoInvalido):
        servicio.crear("asesor.017", "clave-segura-1", "ADMIN")


def test_alias_duplicado() -> None:
    servicio, _ = _servicio()
    servicio.crear("asesor.017", "clave-segura-1", "ASESOR")

    with pytest.raises(UsuarioDuplicado):
        servicio.crear("asesor.017", "otra-clave-123", "CLIENTE")


def test_listar_filtra_por_rol() -> None:
    servicio, _ = _servicio()
    servicio.crear("asesor.017", "clave-segura-1", "ASESOR")
    servicio.crear("cliente.a", "clave-segura-1", "CLIENTE")

    assert [u["usuario"] for u in servicio.listar("CLIENTE")] == ["cliente.a"]
    assert len(servicio.listar(None)) == 2


def test_cambiar_rol_y_suspender() -> None:
    servicio, _ = _servicio()
    usuario = servicio.crear("cliente.a", "clave-segura-1", "CLIENTE")

    assert servicio.cambiar_rol(usuario["id"], "ASESOR")["rol"] == "ASESOR"
    assert servicio.suspender(usuario["id"])["estado"] == "SUSPENDIDO"
    # Baja lógica: el usuario sigue existiendo.
    assert servicio.obtener(usuario["id"])["estado"] == "SUSPENDIDO"


def test_operaciones_sobre_usuario_inexistente_devuelven_none() -> None:
    servicio, _ = _servicio()

    assert servicio.obtener("no-existe") is None
    assert servicio.cambiar_rol("no-existe", "ASESOR") is None
    assert servicio.suspender("no-existe") is None
