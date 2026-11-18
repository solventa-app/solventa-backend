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
TIPOS = {"MEDICO", "MECANICO", "GRUA"}  # v0.49: GRUA
VERSION = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(\d+)$")
CAMPOS = ("nombre", "tipo", "ciudad", "latitud", "longitud", "activo")
# v0.49: lo que muestra el detalle del prestador. Obligatorios en el archivo; `horario` y
# `foto_url` pueden ser null (la app muestra el placeholder del tipo sin foto).
CAMPOS_DETALLE = ("aliado", "especialidad", "direccion", "horario", "foto_url")
# v0.53: la especialidad y el horario como datos, para que la app los traduzca. `especialidad` y
# `horario` (texto en español) se mantienen para las versiones de la app ya instaladas.
CAMPOS_CODIGOS = ("codigo_especialidad", "horario_atencion")
ESPECIALIDADES = {
    "URGENCIAS",
    "MEDICINA_GENERAL",
    "MECANICA_GENERAL",
    "LLANTAS_Y_FRENOS",
    "GRUA_LIVIANA",
    "GRUA_Y_CARRO_TALLER",
}
DIAS = {"LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"}
HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
CAMPOS_HORARIO = {"abierto_24h", "dia_desde", "dia_hasta", "abre", "cierra"}


class ErrorCatalogo(Exception):
    """El archivo del catálogo no es válido o cambió sin subir la versión."""


def _orden(version: str) -> tuple[str, int]:
    coincidencia = VERSION.match(version)
    if coincidencia is None:
        raise ErrorCatalogo(f"versión inválida {version!r}: debe ser AAAA-MM-DD.n")
    return coincidencia.group(1), int(coincidencia.group(2))


def _validar_horario(id_prestador: str, horario: dict | None) -> None:
    """Null (no se publica), abierto 24 h (el resto null) o días y horas completos."""
    if horario is None:
        return
    if not isinstance(horario, dict) or set(horario) != CAMPOS_HORARIO:
        raise ErrorCatalogo(
            f"prestador {id_prestador!r}: horario_atencion debe tener {sorted(CAMPOS_HORARIO)}"
        )
    resto = [horario[c] for c in ("dia_desde", "dia_hasta", "abre", "cierra")]
    if horario["abierto_24h"] is True:
        if any(v is not None for v in resto):
            raise ErrorCatalogo(f"prestador {id_prestador!r}: abierto 24 h sin días ni horas")
        return
    if horario["abierto_24h"] is not False:
        raise ErrorCatalogo(f"prestador {id_prestador!r}: abierto_24h debe ser true o false")
    if horario["dia_desde"] not in DIAS or horario["dia_hasta"] not in DIAS:
        raise ErrorCatalogo(f"prestador {id_prestador!r}: día desconocido en horario_atencion")
    if not all(isinstance(h, str) and HORA.match(h) for h in (horario["abre"], horario["cierra"])):
        raise ErrorCatalogo(f"prestador {id_prestador!r}: abre y cierra en formato HH:MM")


def leer(ruta: Path = ARCHIVO) -> tuple[str, list[dict]]:
    """Lee y valida el archivo. Devuelve la versión y los prestadores."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    version = datos.get("version", "")
    _orden(version)
    prestadores = datos.get("prestadores", [])
    vistos: set[str] = set()
    for p in prestadores:
        faltan = {"id", *CAMPOS, *CAMPOS_DETALLE, *CAMPOS_CODIGOS} - set(p)
        if faltan:
            raise ErrorCatalogo(f"prestador {p.get('id')!r} sin {sorted(faltan)}")
        if p["id"] in vistos:
            raise ErrorCatalogo(f"prestador repetido {p['id']!r}")
        if p["tipo"] not in TIPOS:
            raise ErrorCatalogo(f"prestador {p['id']!r} con tipo desconocido {p['tipo']!r}")
        if p["codigo_especialidad"] not in ESPECIALIDADES:
            raise ErrorCatalogo(
                f"prestador {p['id']!r} con especialidad desconocida {p['codigo_especialidad']!r}"
            )
        _validar_horario(p["id"], p["horario_atencion"])
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
            {"$set": {campo: p[campo] for campo in (*CAMPOS, *CAMPOS_DETALLE, *CAMPOS_CODIGOS)}},
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
