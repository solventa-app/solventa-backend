"""Lectura del catálogo maestro de prestadores (HU-M06, KAN-263).

La escritura es de `migrations.catalogos` (escritor único: CLAIMS). Aquí solo se lee, de la
**secundaria** con tope de tiempo (HA-LAT-003, EC011/EC012), como la bandeja de siniestros.

Sin datos personales: la ubicación del cliente no llega aquí; la app calcula la cercanía.
"""

from pymongo import MongoClient
from pymongo.read_preferences import SecondaryPreferred

CONTROL = "prestadores"


def prestador_desde_documento(doc: dict) -> dict:
    """Un prestador como lo ven el BFF y la app. Los campos de la v0.49 tienen un valor por
    defecto para los documentos que todavía no los tienen (antes de sincronizar el catálogo)."""
    return {
        "id": doc["_id"],
        "nombre": doc["nombre"],
        "tipo": doc["tipo"],
        "ciudad": doc["ciudad"],
        "latitud": float(doc["latitud"]),
        "longitud": float(doc["longitud"]),
        "activo": doc["activo"],
        "aliado": bool(doc.get("aliado", False)),
        "especialidad": doc.get("especialidad") or "",
        "direccion": doc.get("direccion") or "",
        "horario": doc.get("horario"),
        "foto_url": doc.get("foto_url"),
    }


class CatalogoNoDisponible(Exception):
    """Todavía no hay catálogo cargado (no se corrieron las migraciones)."""


class LectorCatalogoPrestadores:
    def __init__(self, cliente: MongoClient, nombre_db: str = "claims", lectura_max_ms: int = 500):
        db = cliente[nombre_db].with_options(read_preference=SecondaryPreferred())
        self._catalogos = db["catalogos"]
        self._prestadores = db["prestadores"]
        self._lectura_max_ms = lectura_max_ms

    def version_vigente(self) -> str:
        control = self._catalogos.find_one(
            {"_id": CONTROL}, {"version": 1}, max_time_ms=self._lectura_max_ms
        )
        if control is None:
            raise CatalogoNoDisponible
        return control["version"]

    def prestadores(self) -> list[dict]:
        cursor = self._prestadores.find({}).sort("_id", 1).max_time_ms(self._lectura_max_ms)
        return [prestador_desde_documento(doc) for doc in cursor]


def consultar(lector, version_local: str | None) -> dict:
    """`version_local` igual a la vigente: `vigente` y lista vacía. Nula o distinta: la lista
    completa, con los inactivos (la app los descarta; así también sabe cuáles dar de baja)."""
    version = lector.version_vigente()
    if version_local == version:
        return {"version": version, "vigente": True, "prestadores": []}
    return {"version": version, "vigente": False, "prestadores": lector.prestadores()}
