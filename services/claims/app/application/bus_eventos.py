"""Bus de eventos de CLAIMS (HU-W04, W04-9): `siniestro.aprobado`, `.rechazado` y `.derivado`.

Cada evento se **valida contra su esquema antes de publicarlo** (HA-INT-004): los esquemas son copia
de `contracts/events/` en `app/eventos/` (`scripts/sincronizar_eventos_siniestros.py`, verificado en
el CI). Un evento que no cumple no se publica y queda un error en el log: es un defecto del código,
no del usuario, y no tumba la resolución ya guardada.

Hoy nadie consume estos eventos y Pub/Sub real solo está aprobado para `perfil.actualizado` (W05-8),
así que el adaptador escribe en el log, igual que POLICY con `poliza.emitida`. Regla #6: los eventos
no llevan datos personales (ni nombre, ni documento, ni ubicación, ni comentario, ni instrucciones)
y el log solo lleva identificadores opacos.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from app.application.identificadores import id_opaco

logger = logging.getLogger("claims.eventos")

ESQUEMAS = Path(__file__).resolve().parent.parent / "eventos"


def _validadores() -> dict[str, Draft202012Validator]:
    validadores = {}
    for ruta in sorted(ESQUEMAS.glob("siniestro.*.schema.json")):
        esquema = json.loads(ruta.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(esquema)
        validadores[esquema["title"]] = Draft202012Validator(
            esquema, format_checker=FormatChecker()
        )
    return validadores


def _evento(tipo: str, datos: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": tipo,
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": datos,
    }


def _comunes(siniestro: dict, actor: str | None) -> dict[str, Any]:
    return {
        "siniestroId": siniestro["id"],
        "radicado": siniestro["radicado"],
        "polizaId": siniestro["poliza_id"],
        "actorUsuarioId": actor or "desconocido",
    }


def evento_aprobado(siniestro: dict, resolucion: dict, actor: str | None) -> dict[str, Any]:
    return _evento(
        "siniestro.aprobado",
        {
            **_comunes(siniestro, actor),
            "resueltaEn": resolucion["resuelta_en"].isoformat(),
            "indemnizacion": resolucion["indemnizacion"],
            "ordenPagoId": resolucion["orden_pago_id"],
        },
    )


def evento_rechazado(siniestro: dict, resolucion: dict, actor: str | None) -> dict[str, Any]:
    return _evento(
        "siniestro.rechazado",
        {
            **_comunes(siniestro, actor),
            "resueltaEn": resolucion["resuelta_en"].isoformat(),
            "motivo": resolucion["motivo"],
        },
    )


def evento_derivado(siniestro: dict, asignacion: dict, actor: str | None) -> dict[str, Any]:
    return _evento(
        "siniestro.derivado",
        {
            **_comunes(siniestro, actor),
            "asignadaEn": asignacion["asignada_en"].isoformat(),
            "peritoId": asignacion["perito_id"],
            "venceInformeEn": asignacion["vence_informe_en"].isoformat(),
        },
    )


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> bool: ...


class BusEventosLog:
    """Valida y escribe el evento en el log (placeholder de Pub/Sub). Devuelve si se publicó."""

    def __init__(self) -> None:
        self._validadores = _validadores()

    def publicar(self, evento: dict[str, Any]) -> bool:
        validador = self._validadores.get(evento.get("tipo", ""))
        if validador is None:
            logger.error("evento sin esquema: %s; no se publica", evento.get("tipo"))
            return False
        errores = sorted(validador.iter_errors(evento), key=lambda e: e.path)
        if errores:
            # Solo la ruta del campo y la regla, nunca el valor (puede llevar datos).
            detalle = "; ".join(
                f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.validator}" for e in errores
            )
            logger.error("evento=%s inválido (%s); no se publica", evento["tipo"], detalle)
            return False
        datos = evento["datos"]
        logger.info(
            "evento=%s eventId=%s siniestro=%s poliza=%s [PLACEHOLDER: sin Pub/Sub real todavia]",
            evento["tipo"],
            evento["eventId"],
            id_opaco(datos["siniestroId"]),
            id_opaco(datos["polizaId"]),
        )
        return True
