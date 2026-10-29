"""Semilla de pólizas de demostración para la mesa de siniestros (HU-W04, W04-3).

Uso: `python -m app.sembrar_demo` (tarea `sembrar-polizas-demo` de docker compose). Solo para local
y pruebas.

POLICY sigue siendo el escritor único de sus tablas: la semilla usa su propio repositorio. Crea 10
propuestas y sus pólizas `EMITIDA` con la prima, la cobertura y el plazo cifrados, como una emisión
normal, con los datos de `semilla_polizas_demo.json` (tomados del dummy de siniestros). Es
idempotente: los ids de propuesta y de oferta son fijos, así que una segunda corrida devuelve las
mismas pólizas sin duplicar nada.

Los clientes son identificadores opacos de demostración (`demo-siniestros-cli-NN`), no titulares
reales.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from app.application.cifrado import cifrador_desde_entorno
from app.application.repositorio_propuestas import RepositorioPropuestasPostgres, construir_pool

DATOS = Path(__file__).resolve().parent / "semilla_polizas_demo.json"

log = logging.getLogger("sembrar_demo")


def _meses(desde: datetime, hasta: datetime) -> int:
    return (hasta.year - desde.year) * 12 + (hasta.month - desde.month)


def sembrar(repositorio, polizas: list[dict]) -> list[dict]:
    """Crea (o recupera) cada propuesta y su póliza. Devuelve `[{propuesta_id, poliza_id}]`."""
    creadas = []
    for p in polizas:
        desde = datetime.fromisoformat(p["vigencia_desde"])
        hasta = datetime.fromisoformat(p["vigencia_hasta"])
        repositorio.crear_o_obtener(
            p["propuesta_id"],
            {
                "oferta_id": p["oferta_id"],
                "cliente_id": p["cliente_id"],
                "tipo": "COMPLETA",
                "prima": p["prima"],
                "cobertura": p["cobertura"],
                "plazo_meses": _meses(desde, hasta),
                "nivel_riesgo": "BAJO",
            },
        )
        poliza, _ = repositorio.emitir_poliza(
            p["propuesta_id"], p["cliente_id"], desde, hasta, desde=("ACEPTADA",)
        )
        creadas.append({"propuesta_id": p["propuesta_id"], "poliza_id": poliza["id"]})
    return creadas


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s [%(name)s] %(message)s")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("Falta DATABASE_URL")
        return 1
    pool = construir_pool(database_url)
    try:
        repositorio = RepositorioPropuestasPostgres(pool, cifrador_desde_entorno())
        polizas = json.loads(DATOS.read_text(encoding="utf-8"))
        creadas = sembrar(repositorio, polizas)
    finally:
        pool.close()
    log.info("Pólizas de demostración listas: %d", len(creadas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
