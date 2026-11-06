"""Punto de entrada: `python -m migrations`."""

import logging
import os
import sys

from pymongo import MongoClient

from . import aplicar_pendientes
from .catalogos import ErrorCatalogo, sincronizar_prestadores

log = logging.getLogger("migraciones")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
    uri = os.environ.get("MONGO_URI")
    if not uri:
        log.error("Falta MONGO_URI")
        return 1
    nombre_db = os.environ.get("MONGO_DB", "claims")
    cliente = MongoClient(uri, serverSelectionTimeoutMS=15_000)
    db = cliente[nombre_db]
    aplicadas = aplicar_pendientes(db)
    log.info("Migraciones aplicadas: %s", ", ".join(aplicadas) if aplicadas else "ninguna (al día)")
    # Datos maestros (HU-M06): se sincronizan en cada ejecución, no una sola vez.
    try:
        log.info("Catálogos: %s", sincronizar_prestadores(db))
    except ErrorCatalogo as error:
        log.error("Catálogo de prestadores: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
