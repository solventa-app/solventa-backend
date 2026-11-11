"""RepositorioDispositivosPostgres contra PostgreSQL real (KAN-26), si `AUTH_TEST_DATABASE_URL` da
una base migrada. Las demás pruebas de dispositivos usan un repositorio falso, que no detecta cómo
devuelve las filas el pool real (`dict_row`)."""

import os
import uuid
from datetime import UTC, datetime

import pytest

from app.dispositivos import RepositorioDispositivosPostgres
from app.usuarios import construir_pool

URL_PRUEBA = os.getenv("AUTH_TEST_DATABASE_URL")
con_postgres = pytest.mark.skipif(not URL_PRUEBA, reason="sin AUTH_TEST_DATABASE_URL")


@pytest.fixture
def pool():
    pool = construir_pool(URL_PRUEBA)
    yield pool
    pool.close()


def _usuario(pool) -> str:
    usuario_id = str(uuid.uuid4())
    with pool.connection() as conexion:
        conexion.execute(
            "INSERT INTO usuarios (id, usuario, hash_clave, rol) VALUES (%s, %s, 'x', 'CLIENTE')",
            (usuario_id, f"prueba.{usuario_id[:8]}"),
        )
    return usuario_id


@con_postgres
def test_postgres_obtener_activo_devuelve_la_fila(pool) -> None:
    repositorio = RepositorioDispositivosPostgres(pool)
    usuario_id = _usuario(pool)
    dispositivo_id = str(uuid.uuid4())
    repositorio.registrar(dispositivo_id, usuario_id, "clave-publica")

    fila = repositorio.obtener_activo(dispositivo_id)

    assert fila == {
        "id": dispositivo_id,
        "usuario_id": usuario_id,
        "clave_publica": "clave-publica",
    }


@con_postgres
def test_postgres_revocado_o_inexistente_no_esta_activo(pool) -> None:
    repositorio = RepositorioDispositivosPostgres(pool)
    usuario_id = _usuario(pool)
    dispositivo_id = str(uuid.uuid4())
    repositorio.registrar(dispositivo_id, usuario_id, "clave-publica")

    repositorio.revocar_de_usuario(usuario_id, datetime.now(UTC))

    assert repositorio.obtener_activo(dispositivo_id) is None
    assert repositorio.obtener_activo(str(uuid.uuid4())) is None
