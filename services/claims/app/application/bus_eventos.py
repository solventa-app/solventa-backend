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


# KAN-259 (HU-M04, Fase 4): además de `siniestro.*`, CLAIMS valida el evento que consume
# (`evento.parametrico.recibido`) y el que publica para liquidar la indemnización
# (`notificacion.push-solicitada`). Sus copias las trae
# `scripts/sincronizar_evento_parametrico_claims.py`, no el glob de `siniestro.*`.
_PATRONES = (
    "siniestro.*.schema.json",
    "evento.parametrico.recibido.schema.json",
    "notificacion.push-solicitada.schema.json",
)


def _validadores() -> dict[str, Draft202012Validator]:
    validadores = {}
    for patron in _PATRONES:
        for ruta in sorted(ESQUEMAS.glob(patron)):
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


def evento_push_indemnizacion(
    usuario_id: str, siniestro_id: str, monto: dict[str, str]
) -> dict[str, Any]:
    """KAN-259 (HU-M04, Fase 4): al liquidar un siniestro paramétrico, pide el push que
    `enviar-push` manda por SNS/FCM (plantilla `indemnizacion-liquidada`, ruta `/m04-02/{id}`,
    ver `contracts/plantillas-push.json`). `idempotencia` es el id del siniestro: una reentrega del
    evento reemplaza la notificación en el teléfono, no la duplica."""
    return _evento(
        "notificacion.push-solicitada",
        {
            "idempotencia": siniestro_id,
            "usuarioId": usuario_id,
            "plantilla": "indemnizacion-liquidada",
            "variables": {"monto": monto["valor"], "moneda": monto["moneda"]},
            "ruta": f"/m04-02/{siniestro_id}",
        },
    )


def evento_reportado(siniestro: dict, fotos: int) -> dict[str, Any]:
    """HU-M05: el cliente reportó el caso desde la app. Sin datos personales: ni el relato ni la
    ubicación viajan en el evento."""
    return _evento(
        "siniestro.reportado",
        {
            **_comunes(siniestro, siniestro["reportado_por"]),
            "notificadoEn": siniestro["notificado_en"].isoformat(),
            "venceDictamenEn": siniestro["vence_dictamen_en"].isoformat(),
            "fotos": fotos,
            "canal": "app",
        },
    )


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> bool: ...


_VALIDADORES_COMPARTIDOS: dict[str, Draft202012Validator] | None = None


def validar_entrante(evento: dict[str, Any]) -> list[str]:
    """KAN-259 (HU-M04, Fase 4): valida un evento que CLAIMS **consume**
    (`evento.parametrico.recibido`) contra su esquema, antes de procesarlo (lo llama
    `POST /eventos/parametricos` sin necesitar una instancia del bus). Lista de errores (ruta y
    regla, nunca el valor); vacía si cumple."""
    global _VALIDADORES_COMPARTIDOS
    if _VALIDADORES_COMPARTIDOS is None:
        _VALIDADORES_COMPARTIDOS = _validadores()
    validador = _VALIDADORES_COMPARTIDOS.get(evento.get("tipo", ""))
    if validador is None:
        return [f"sin esquema para tipo={evento.get('tipo')!r}"]
    errores = sorted(validador.iter_errors(evento), key=lambda e: e.path)
    return [f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.validator}" for e in errores]


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
        # `siniestro.*` lleva siniestroId/polizaId; `notificacion.push-solicitada` (KAN-259, Fase
        # 4) lleva usuarioId. Ninguno es un dato personal (regla #6).
        if "siniestroId" in datos:
            referencia = (
                f"siniestro={id_opaco(datos['siniestroId'])} poliza={id_opaco(datos['polizaId'])}"
            )
        else:
            referencia = f"usuario={id_opaco(datos.get('usuarioId', ''))}"
        logger.info(
            "evento=%s eventId=%s %s [PLACEHOLDER: sin Pub/Sub real todavia]",
            evento["tipo"],
            evento["eventId"],
            referencia,
        )
        return True
