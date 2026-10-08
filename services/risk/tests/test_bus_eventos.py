"""El evento `perfil.actualizado` debe validar contra
`contracts/events/perfil.actualizado.schema.json` tal cual: si el bus real
(Pub/Sub) llega algún día, el payload ya es correcto (ver bus_eventos.py)."""

import json
import logging
from pathlib import Path

import jsonschema
import pytest

from app.application.bus_eventos import BusEventosLog, construir_evento_perfil_actualizado

RUTA_ESQUEMA = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "events"
    / "perfil.actualizado.schema.json"
)


@pytest.fixture
def esquema() -> dict:
    with RUTA_ESQUEMA.open(encoding="utf-8") as archivo:
        return json.load(archivo)


def test_evento_construido_valida_contra_el_contrato(esquema: dict) -> None:
    evento = construir_evento_perfil_actualizado(
        cliente_id="cliente-123", perfil_version=1, operation_time="1733600000.1"
    )

    jsonschema.validate(evento, esquema)


def test_evento_lleva_los_campos_correctos() -> None:
    evento = construir_evento_perfil_actualizado(
        cliente_id="cliente-123", perfil_version=4, operation_time="42.0"
    )

    assert evento["tipo"] == "perfil.actualizado"
    assert evento["version"] == 1
    assert evento["datos"] == {
        "clienteId": "cliente-123",
        "perfilVersion": 4,
        "operationTime": "42.0",
    }


def test_bus_log_no_filtra_el_cliente_id_en_crudo(caplog: pytest.LogCaptureFixture) -> None:
    evento = construir_evento_perfil_actualizado(
        cliente_id="cliente-muy-personal-123", perfil_version=1, operation_time="1.0"
    )

    with caplog.at_level(logging.INFO, logger="risk.eventos"):
        BusEventosLog().publicar(evento)

    mensajes = " ".join(registro.getMessage() for registro in caplog.records)
    assert "cliente-muy-personal-123" not in mensajes
