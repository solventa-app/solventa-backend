"""El evento `cobro.estado-cambiado` debe validar contra
`contracts/events/cobro.estado-cambiado.schema.json` tal cual: si el bus real
(Pub/Sub) llega algún día, el payload ya es correcto (ver bus_eventos.py)."""

import json
import logging
from pathlib import Path

import jsonschema
import pytest

from app.application.bus_eventos import BusEventosLog, construir_evento_cobro_estado_cambiado

RUTA_ESQUEMA = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "events"
    / "cobro.estado-cambiado.schema.json"
)


@pytest.fixture
def esquema() -> dict:
    with RUTA_ESQUEMA.open(encoding="utf-8") as archivo:
        return json.load(archivo)


@pytest.mark.parametrize("estado", ["cobrando", "cobrado", "rechazado", "pendiente"])
def test_evento_construido_valida_contra_el_contrato(esquema: dict, estado: str) -> None:
    evento = construir_evento_cobro_estado_cambiado(
        cobro_id="cobro-123", poliza_id="poliza-abc", estado=estado, clave_idempotencia="clave-1"
    )

    jsonschema.validate(evento, esquema)


def test_evento_lleva_los_campos_correctos() -> None:
    evento = construir_evento_cobro_estado_cambiado(
        cobro_id="cobro-123", poliza_id="poliza-abc", estado="cobrado", clave_idempotencia="clave-1"
    )

    assert evento["tipo"] == "cobro.estado-cambiado"
    assert evento["version"] == 1
    assert evento["datos"] == {
        "cobroId": "cobro-123",
        "polizaId": "poliza-abc",
        "estado": "cobrado",
        "claveIdempotencia": "clave-1",
    }


def test_bus_log_no_filtra_la_poliza_en_crudo(caplog: pytest.LogCaptureFixture) -> None:
    evento = construir_evento_cobro_estado_cambiado(
        cobro_id="cobro-123",
        poliza_id="poliza-muy-identificable-456",
        estado="cobrado",
        clave_idempotencia="clave-1",
    )

    with caplog.at_level(logging.INFO, logger="payments.eventos"):
        BusEventosLog().publicar(evento)

    mensajes = " ".join(registro.getMessage() for registro in caplog.records)
    assert "poliza-muy-identificable-456" not in mensajes
