"""Punto de entrada: `python -m migrations`."""

import logging
import os
import sys

from pymongo import MongoClient

from . import aplicar_pendientes

log = logging.getLogger("migraciones")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
    uri = os.environ.get("MONGO_URI")
    if not uri:
        log.error("Falta MONGO_URI")
        return 1
    nombre_db = os.environ.get("MONGO_DB", "risk")
    cliente = MongoClient(uri, serverSelectionTimeoutMS=15_000)
    aplicadas = aplicar_pendientes(cliente[nombre_db])
    log.info("Migraciones aplicadas: %s", ", ".join(aplicadas) if aplicadas else "ninguna (al día)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
