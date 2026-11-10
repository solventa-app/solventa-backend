"""Semilla de pólizas de demostración para la mesa de siniestros (HU-W04, W04-3).

Uso: `python -m app.sembrar_demo` (tarea `sembrar-polizas-demo` de docker compose). Solo para local
y pruebas.

POLICY sigue siendo el escritor único de sus tablas: la semilla usa su propio repositorio. Crea 20
propuestas y sus pólizas `EMITIDA` con la prima, la cobertura y el plazo cifrados, como una emisión
normal, con los datos de `semilla_polizas_demo.json`. Es
idempotente: los ids de propuesta y de oferta son fijos, así que una segunda corrida devuelve las
mismas pólizas sin duplicar nada.

Los clientes son identificadores opacos de demostración (`demo-siniestros-cli-NN`), no titulares
reales.

HU-M05: con `DEMO_CLIENTE_DOCUMENTO` agrega además una póliza a nombre del CLIENTE demo de AUTH
(`crear-cliente-siniestros-demo`): su `cliente_id` es el titular, el mismo HMAC del documento con
`CLIENTE_ID_CLAVE` que calculan AUTH y el BFF. Así ese cliente la ve en `propuestas` y puede
reportar un siniestro sobre ella desde el móvil.
"""

import hashlib
import hmac
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
                # KAN-290: la póliza de auto de demo lleva su vehículo y sus coberturas.
                "producto": p.get("producto", "VIDA_DEUDOR"),
                "vehiculo": p.get("vehiculo"),
                "coberturas": p.get("coberturas"),
            },
        )
        poliza, _ = repositorio.emitir_poliza(
            p["propuesta_id"], p["cliente_id"], desde, hasta, desde=("ACEPTADA",)
        )
        creadas.append({"propuesta_id": p["propuesta_id"], "poliza_id": poliza["id"]})
    return creadas


def poliza_cliente_demo(documento: str, clave: str) -> dict:
    """La póliza del CLIENTE demo de HU-M05, vigente todo 2026 (mismo algoritmo de titular que
    AUTH y el BFF: HMAC-SHA256 del documento)."""
    titular = hmac.new(
        clave.encode("utf-8"), documento.strip().encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "propuesta_id": "demo-m05-prop-01",
        "oferta_id": "demo-m05-oferta-01",
        "cliente_id": titular,
        "vigencia_desde": "2026-01-01T00:00:00-05:00",
        "vigencia_hasta": "2027-01-01T00:00:00-05:00",
        "prima": {"valor": "1320000", "moneda": "COP"},
        "cobertura": {"valor": "45000000", "moneda": "COP"},
    }


def poliza_auto_cliente_demo(documento: str, clave: str) -> dict:
    """KAN-290: la póliza de auto todo riesgo del CLIENTE demo (el Mazda 3 2024 de la placa de demo
    `ABC123` de stub-vehiculos), para reportar un choque desde la app y pedir grúa."""
    poliza = poliza_cliente_demo(documento, clave)
    valor = {"valor": "118500000.00", "moneda": "COP"}
    deducible = "10 % mín. 1 SMMLV"
    return {
        **poliza,
        "propuesta_id": "demo-auto-prop-01",
        "oferta_id": "demo-auto-oferta-01",
        "prima": {"valor": "332198.00", "moneda": "COP"},
        "cobertura": valor,
        "producto": "AUTO",
        "vehiculo": {
            "placaEnmascarada": "ABC•••",
            "marca": "MAZDA",
            "linea": "3 GRAND TOURING",
            "modelo": 2024,
            "clase": "AUTOMOVIL",
            "uso": "PARTICULAR",
            "valorComercial": valor,
            "ciudadCirculacion": "Bogotá",
            "kilometrajeAnual": 12000,
            "deducible": "DIEZ_POR_CIENTO",
        },
        "coberturas": [
            {
                "tipo": "RESPONSABILIDAD_CIVIL",
                "valorAsegurado": {"valor": "1000000000.00", "moneda": "COP"},
                "deducible": None,
            },
            *[
                {"tipo": tipo, "valorAsegurado": valor, "deducible": deducible}
                for tipo in (
                    "PERDIDA_TOTAL_DANOS",
                    "PERDIDA_PARCIAL_DANOS",
                    "PERDIDA_TOTAL_HURTO",
                    "PERDIDA_PARCIAL_HURTO",
                )
            ],
            {"tipo": "ASISTENCIA", "valorAsegurado": None, "deducible": None},
        ],
    }


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
        documento = os.environ.get("DEMO_CLIENTE_DOCUMENTO", "").strip()
        if documento:
            clave = os.environ.get("CLIENTE_ID_CLAVE", "solo-local-no-secreta")
            polizas.append(poliza_cliente_demo(documento, clave))
            polizas.append(poliza_auto_cliente_demo(documento, clave))
        creadas = sembrar(repositorio, polizas)
    finally:
        pool.close()
    log.info("Pólizas de demostración listas: %d", len(creadas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
