"""Construye y valida `evento.parametrico.recibido` (KAN-259, Fase 3) contra su esquema (copia
sincronizada en `app/`, ver `scripts/sincronizar_contrato_eventos_parametricos.py`). Mismo patrón
que `enviar-push/app/esquema_evento.py`: nada se publica si no cumple el contrato."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

RUTA_ESQUEMA = Path(__file__).resolve().parent.parent / "evento.parametrico.recibido.schema.json"

TipoEvento = Literal["VUELO", "CLIMA"]


class EventoInvalido(Exception):
    def __init__(self, detalle: str) -> None:
        super().__init__(detalle)
        self.detalle = detalle


def _validador(ruta: Path = RUTA_ESQUEMA) -> Draft202012Validator:
    esquema = json.loads(ruta.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(esquema)
    return Draft202012Validator(esquema, format_checker=FormatChecker())


_VALIDADOR = _validador()


def construir_evento_clima(
    clave: str, variable: str, valor: float, medido_en: str
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "evento.parametrico.recibido",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": {
            "tipo": "CLIMA",
            "clave": clave,
            "valor": valor,
            "medidoEn": medido_en,
            "fuente": "open-meteo",
            "variable": variable,
        },
    }


def construir_evento_vuelo(
    numero_vuelo: str, fecha: str, retraso_minutos: int, cancelado: bool, medido_en: str
) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "evento.parametrico.recibido",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": {
            "tipo": "VUELO",
            "clave": f"{numero_vuelo}:{fecha}",
            "valor": float(retraso_minutos),
            "medidoEn": medido_en,
            "fuente": "aviationstack",
            "cancelado": cancelado,
        },
    }


def validar(evento: dict[str, Any], validador: Draft202012Validator = _VALIDADOR) -> None:
    errores = sorted(validador.iter_errors(evento), key=lambda e: e.path)
    if errores:
        detalle = "; ".join(
            f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.validator}" for e in errores
        )
        raise EventoInvalido(detalle)
