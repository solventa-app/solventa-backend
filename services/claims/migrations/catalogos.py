"""Sincroniza el catálogo maestro de prestadores (HU-M06, KAN-263) desde `datos/prestadores.json`.

Corre en cada `python -m migrations`, después de las migraciones (que crean las colecciones en
`0005_prestadores`). Es el mismo comando en local, CI y staging, así que no hace falta un job
aparte para sembrar.

Reglas:

- **Upsert por `id`, nunca borra.** Dar de baja un prestador es `activo: false` en el archivo.
- **La versión manda.** Con la misma versión y el mismo contenido no toca nada. Con la misma
  versión y otro contenido **falla**: la app no volvería a descargar el catálogo, así que hay que
  subir la versión (`AAAA-MM-DD.n`).
- **Solo hacia adelante.** Si la base ya tiene una versión más nueva (p. ej. una revisión anterior
  migrando durante un despliegue), no la baja.
- Primero los prestadores y al final la versión: un lector nunca ve una versión sin sus datos.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("migraciones.catalogos")

ARCHIVO = Path(__file__).resolve().parent / "datos" / "prestadores.json"
CONTROL = "prestadores"
TIPOS = {"MEDICO", "MECANICO", "GRUA"}  # v0.46: GRUA
VERSION = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(\d+)$")
CAMPOS = ("nombre", "tipo", "ciudad", "latitud", "longitud", "activo")
# v0.46: lo que muestra el detalle del prestador. Obligatorios en el archivo; `horario` y
# `foto_url` pueden ser null (la app muestra el placeholder del tipo sin foto).
CAMPOS_DETALLE = ("aliado", "especialidad", "direccion", "horario", "foto_url")


class ErrorCatalogo(Exception):
    """El archivo del catálogo no es válido o cambió sin subir la versión."""


def _orden(version: str) -> tuple[str, int]:
    coincidencia = VERSION.match(version)
    if coincidencia is None:
        raise ErrorCatalogo(f"versión inválida {version!r}: debe ser AAAA-MM-DD.n")
    return coincidencia.group(1), int(coincidencia.group(2))


def leer(ruta: Path = ARCHIVO) -> tuple[str, list[dict]]:
    """Lee y valida el archivo. Devuelve la versión y los prestadores."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    version = datos.get("version", "")
    _orden(version)
    prestadores = datos.get("prestadores", [])
    vistos: set[str] = set()
    for p in prestadores:
        faltan = {"id", *CAMPOS, *CAMPOS_DETALLE} - set(p)
        if faltan:
            raise ErrorCatalogo(f"prestador {p.get('id')!r} sin {sorted(faltan)}")
        if p["id"] in vistos:
            raise ErrorCatalogo(f"prestador repetido {p['id']!r}")
        if p["tipo"] not in TIPOS:
            raise ErrorCatalogo(f"prestador {p['id']!r} con tipo desconocido {p['tipo']!r}")
        vistos.add(p["id"])
    return version, prestadores


def huella(prestadores: list[dict]) -> str:
    """SHA-256 del contenido, independiente del orden de la lista y de las llaves."""
    canonico = json.dumps(
        sorted(prestadores, key=lambda p: p["id"]), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def sincronizar_prestadores(db, ruta: Path = ARCHIVO, ahora: datetime | None = None) -> str:
    """Deja la base con el catálogo del archivo. Devuelve qué hizo (para el log)."""
    version, prestadores = leer(ruta)
    firma = huella(prestadores)
    control = db["catalogos"].find_one({"_id": CONTROL})
    if control is not None:
        if control["version"] == version:
            if control["huella"] == firma:
                return f"prestadores {version}: al día"
            raise ErrorCatalogo(
                f"el catálogo de prestadores cambió pero sigue en la versión {version}: "
                "sube `version` en migrations/datos/prestadores.json"
            )
        if _orden(control["version"]) > _orden(version):
            return f"prestadores: la base ya tiene {control['version']} (más nueva que {version})"

    for p in prestadores:
        db["prestadores"].update_one(
            {"_id": p["id"]},
            {"$set": {campo: p[campo] for campo in (*CAMPOS, *CAMPOS_DETALLE)}},
            upsert=True,
        )
    db["catalogos"].update_one(
        {"_id": CONTROL},
        {
            "$set": {
                "version": version,
                "huella": firma,
                "actualizado_en": ahora or datetime.now(UTC),
            }
        },
        upsert=True,
    )
    log.info("Catálogo de prestadores en la versión %s (%d prestadores)", version, len(prestadores))
    return f"prestadores {version}: {len(prestadores)} sincronizados"
