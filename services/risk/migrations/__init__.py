"""Migraciones de MongoDB de RISK (único escritor de este almacén; RATING solo lee).

Cada migración es un módulo `versiones/NNNN_descripcion.py` con `aplicar(db)`. Reglas:

- **Idempotentes**: crear un índice o colección que ya existe no falla. Así dos ejecuciones
  simultáneas (reintento del job) son seguras sin necesidad de bloqueo.
- **Compatibles hacia atrás**: expandir primero, contraer en un despliegue posterior (ADR-07),
  porque la revisión anterior del servicio sigue atendiendo mientras se migra.
- Quedan registradas en la colección `_migraciones`; una migración aplicada no se repite.

Uso: `python -m migrations` (lee MONGO_URI y MONGO_DB). Mismo comando en local, CI y staging.
"""

import importlib
import pkgutil
from collections.abc import Callable
from datetime import UTC, datetime

from . import versiones

COLECCION = "_migraciones"

Migracion = tuple[str, Callable]


def descubrir() -> list[Migracion]:
    """Migraciones del paquete `versiones`, ordenadas por nombre (el prefijo fija el orden)."""
    nombres = sorted(m.name for m in pkgutil.iter_modules(versiones.__path__))
    return [(n, importlib.import_module(f"{versiones.__name__}.{n}").aplicar) for n in nombres]


def aplicar_pendientes(db, migraciones: list[Migracion] | None = None) -> list[str]:
    """Aplica en orden las migraciones aún no registradas y devuelve los nombres aplicados."""
    registro = db[COLECCION]
    hechas = {doc["_id"] for doc in registro.find({}, {"_id": 1})}
    aplicadas = []
    for nombre, aplicar in migraciones if migraciones is not None else descubrir():
        if nombre in hechas:
            continue
        aplicar(db)
        registro.update_one(
            {"_id": nombre}, {"$setOnInsert": {"aplicada_en": datetime.now(UTC)}}, upsert=True
        )
        aplicadas.append(nombre)
    return aplicadas
