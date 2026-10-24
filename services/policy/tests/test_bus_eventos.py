"""`poliza.emitida` debe validar contra `contracts/events/poliza.emitida.schema.json` tal cual."""

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

from app.application.bus_eventos import BusEventosLog, construir_evento_poliza_emitida

RUTA_ESQUEMA = (
    Path(__file__).resolve().parents[3] / "contracts" / "events" / "poliza.emitida.schema.json"
)
DESDE = datetime(2026, 10, 22, tzinfo=UTC)
HASTA = datetime(2031, 10, 22, tzinfo=UTC)


def _evento() -> dict:
    return construir_evento_poliza_emitida(
        "poliza-1", "SOL-2026-000001", "propuesta-1", "cliente-opaco", DESDE, HASTA
    )


def test_el_evento_valida_contra_el_contrato() -> None:
    jsonschema.validate(_evento(), json.loads(RUTA_ESQUEMA.read_text(encoding="utf-8")))


def test_el_evento_lleva_los_campos_correctos() -> None:
    evento = _evento()

    assert evento["tipo"] == "poliza.emitida"
    assert evento["version"] == 1
    assert evento["datos"]["numeroPoliza"] == "SOL-2026-000001"
    assert evento["datos"]["vigenciaHasta"] == HASTA.isoformat()


def test_el_log_redacta_los_identificadores(caplog) -> None:
    caplog.set_level("INFO")

    BusEventosLog().publicar(_evento())

    assert "poliza.emitida" in caplog.text
    assert "cliente-opaco" not in caplog.text
    assert "SOL-2026-000001" not in caplog.text
    assert "propuesta-1" not in caplog.text
