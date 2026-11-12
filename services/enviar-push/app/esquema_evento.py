"""Valida `notificacion.push-solicitada` contra su esquema (copia sincronizada en
`app/contrato/`, ver `scripts/sincronizar_contrato_push.py`). Mismo patrón que
`claims/app/application/bus_eventos.py`: HA-INT-004, nada se publica ni se procesa si no
cumple el contrato."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

RUTA_ESQUEMA = (
    Path(__file__).resolve().parent / "contrato" / "notificacion.push-solicitada.schema.json"
)


class EventoInvalido(Exception):
    def __init__(self, detalle: str) -> None:
        super().__init__(detalle)
        self.detalle = detalle


def _validador(ruta: Path = RUTA_ESQUEMA) -> Draft202012Validator:
    esquema = json.loads(ruta.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(esquema)
    return Draft202012Validator(esquema, format_checker=FormatChecker())


_VALIDADOR = _validador()


def construir_evento(
    idempotencia: str,
    usuario_id: str,
    plantilla: str,
    variables: dict[str, str] | None = None,
    ruta: str | None = None,
    ttl_segundos: int | None = None,
    silenciosa: bool | None = None,
) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "idempotencia": idempotencia,
        "usuarioId": usuario_id,
        "plantilla": plantilla,
    }
    if variables is not None:
        datos["variables"] = variables
    if ruta is not None:
        datos["ruta"] = ruta
    if ttl_segundos is not None:
        datos["ttlSegundos"] = ttl_segundos
    if silenciosa is not None:
        datos["silenciosa"] = silenciosa
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "notificacion.push-solicitada",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": datos,
    }


def validar(evento: dict[str, Any], validador: Draft202012Validator = _VALIDADOR) -> None:
    errores = sorted(validador.iter_errors(evento), key=lambda e: e.path)
    if errores:
        detalle = "; ".join(
            f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.validator}" for e in errores
        )
        raise EventoInvalido(detalle)
