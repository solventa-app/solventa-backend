"""Bus de eventos de POLICY — PLACEHOLDER.

Publica `poliza.emitida` cuando una póliza queda emitida. Hoy nadie la consume (la notificación al
cliente la dispara POLICY directamente en el bloque 5), así que el adaptador solo loguea el evento
ya serializado. Es la misma decisión que PAYMENTS con `cobro.estado-cambiado`: Pub/Sub real solo
está aprobado para `perfil.actualizado` (decisión W05-8), y ampliarlo es una decisión del equipo
(`docs/plan-completar-hu-w03.md`, W03-7). Cuando exista, se sustituye `BusEventosLog` por un
publicador real detrás del mismo `PuertoBusEventos`, sin tocar `ServicioEmision`.

El evento cumple exactamente `contracts/events/poliza.emitida.schema.json` (validado con
`jsonschema` en `tests/test_bus_eventos.py`). Regla #6: el log lleva solo identificadores opacos
(`id_opaco`), nunca el cliente ni el número de póliza en claro."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("policy.eventos")


def construir_evento_poliza_emitida(
    poliza_id: str,
    numero_poliza: str,
    propuesta_id: str,
    cliente_id: str,
    vigencia_desde: datetime,
    vigencia_hasta: datetime,
) -> dict[str, Any]:
    """Construye el payload de `poliza.emitida` (contrato v1)."""
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "poliza.emitida",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": {
            "polizaId": poliza_id,
            "numeroPoliza": numero_poliza,
            "propuestaId": propuesta_id,
            "clienteId": cliente_id,
            "vigenciaDesde": vigencia_desde.isoformat(),
            "vigenciaHasta": vigencia_hasta.isoformat(),
        },
    }


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> None: ...


class BusEventosLog:
    """Adaptador PLACEHOLDER: loguea una versión redactada del evento en vez de publicarlo."""

    def publicar(self, evento: dict[str, Any]) -> None:
        datos = evento["datos"]
        logger.info(
            "evento=%s eventId=%s poliza=%s propuesta=%s cliente=%s "
            "[PLACEHOLDER: sin Pub/Sub real todavia]",
            evento["tipo"],
            evento["eventId"],
            id_opaco(datos["polizaId"]),
            id_opaco(datos["propuestaId"]),
            id_opaco(datos["clienteId"]),
        )
