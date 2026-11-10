"""Crea un usuario desde la línea de comandos (arranque del primer BACKOFFICE).

La gestión de usuarios por la API exige un BACKOFFICE, así que el primero se crea aquí.
Corre como tarea de un solo uso (`crear-backoffice` en docker-compose; en staging, un
Cloud Run Job con la misma imagen), igual que las migraciones.

Uso:
    CLAVE_INICIAL=... python -m app.crear_usuario --usuario admin.solventa --rol BACKOFFICE \\
        --correo admin@solventa.local

Todos entran con correo y clave: el correo es obligatorio. Un asesor u operador no lo valida
(se lo da quien lo crea).

La clave llega por la variable `CLAVE_INICIAL` y nunca como argumento: los argumentos
quedan en el historial de la terminal y en la lista de procesos.

Es idempotente: si el alias ya existe, no hace nada y termina bien (código 0). Así la
tarea se puede repetir en cada `docker compose up` sin fallar."""

import argparse
import logging
import os
import sys

from argon2 import PasswordHasher

from app.cifrado import construir_cifrador
from app.configuracion import Configuracion
from app.usuarios import (
    ROLES,
    DatoInvalido,
    RepositorioUsuarios,
    RepositorioUsuariosPostgres,
    ServicioUsuarios,
    UsuarioDuplicado,
    construir_pool,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("auth.crear_usuario")


def crear_si_no_existe(
    repositorio: RepositorioUsuarios,
    alias: str,
    clave: str,
    rol: str,
    correo: str | None = None,
    servicio: ServicioUsuarios | None = None,
) -> bool:
    """`True` si lo creó; `False` si ya existía."""
    try:
        (servicio or ServicioUsuarios(repositorio, PasswordHasher())).crear(
            alias, clave, rol, correo=correo
        )
    except UsuarioDuplicado:
        return False
    return True


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crea un usuario de Solventa (AUTH).")
    parser.add_argument(
        "--usuario", required=True, help="Alias de acceso (no un correo ni un documento)"
    )
    parser.add_argument("--rol", required=True, choices=ROLES)
    parser.add_argument("--correo", required=True, help="Correo con el que inicia sesión")
    opciones = parser.parse_args(argumentos)

    clave = os.getenv("CLAVE_INICIAL")
    if not clave:
        logger.error("falta la variable de entorno CLAVE_INICIAL")
        return 2

    config = Configuracion.desde_entorno()
    pool = construir_pool(config.database_url)
    try:
        repositorio = RepositorioUsuariosPostgres(pool)
        servicio = ServicioUsuarios(
            repositorio, PasswordHasher(), construir_cifrador(config), config.cliente_id_clave
        )
        creado = crear_si_no_existe(
            repositorio, opciones.usuario, clave, opciones.rol, opciones.correo, servicio
        )
    except DatoInvalido as error:
        logger.error("datos inválidos: %s", error)
        return 2
    finally:
        pool.close()

    logger.info("usuario %s rol=%s", "creado" if creado else "ya existía", opciones.rol)
    return 0


if __name__ == "__main__":
    sys.exit(main())
