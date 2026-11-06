"""Crea el CLIENTE de demostración de HU-M05 (reporte de siniestro desde el móvil). Solo local.

Uso (tarea `crear-cliente-siniestros-demo` de docker compose):
    CLAVE_INICIAL=... python -m app.crear_cliente_demo --alias cliente.siniestros \\
        --correo cliente.siniestros@demo.solventa.co --nombre "María Fernanda Ríos" \\
        --documento 1090000001

Es un CLIENTE verificado, como los que crea un BACKOFFICE (W03-36): con su documento (titular) y
su correo, cifrados. Además se le deja entrar **con el correo** desde la app, como las cuentas del
onboarding (HU-M01), guardando el HMAC del correo. La póliza que reporta la crea la semilla de
POLICY a nombre de su titular (`DEMO_CLIENTE_DOCUMENTO`, mismo documento y misma
`CLIENTE_ID_CLAVE`).

Datos inventados, sin personas reales. Idempotente: si el alias ya existe, solo asegura el HMAC del
correo y termina bien.
"""

import argparse
import logging
import os
import sys

from argon2 import PasswordHasher

from app.cifrado import construir_cifrador
from app.configuracion import Configuracion
from app.usuarios import (
    DatoInvalido,
    RepositorioUsuariosPostgres,
    ServicioUsuarios,
    UsuarioDuplicado,
    construir_pool,
    correo_hmac,
    normalizar_correo,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("auth.crear_cliente_demo")


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CLIENTE demo de HU-M05 (solo local).")
    parser.add_argument("--alias", required=True)
    parser.add_argument("--correo", required=True)
    parser.add_argument("--nombre", required=True)
    parser.add_argument("--documento", required=True)
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
        try:
            servicio.crear(
                opciones.alias,
                clave,
                "CLIENTE",
                nombre=opciones.nombre,
                correo=opciones.correo,
                documento=opciones.documento,
            )
            logger.info("cliente demo creado")
        except UsuarioDuplicado:
            logger.info("cliente demo ya existía")
        usuario = repositorio.obtener_por_alias(opciones.alias)
        if usuario is None:
            logger.error("no se encontró el cliente demo después de crearlo")
            return 1
        huella = correo_hmac(normalizar_correo(opciones.correo), config.cliente_id_clave)
        with pool.connection() as conexion:
            conexion.execute(
                "UPDATE usuarios SET correo_hmac = %(h)s WHERE id = %(id)s",
                {"h": huella, "id": usuario["id"]},
            )
        logger.info("el cliente demo entra con su correo desde la app")
    except DatoInvalido as error:
        logger.error("datos inválidos: %s", error)
        return 2
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
