"""Bus de eventos de RISK — PLACEHOLDER.

No existe infraestructura de Pub/Sub todavía (sin proyecto GCP, ver
`CLAUDE.md`). Esta es una interfaz mínima (función/clase, sin puertos y
adaptadores formales — regla de arquitectura #3: hexagonal es solo para
`acl-worker`) con un adaptador que por ahora solo loguea el evento ya
serializado. Cuando `infra-engineer` levante Pub/Sub real, se sustituye
`BusEventosLog` por un publicador real detrás del mismo `PuertoBusEventos`,
sin tocar `ServicioPerfiles` (el caso de uso que lo invoca).

El evento que se publica (`evento_perfil_actualizado`) cumple exactamente el
esquema de `contracts/events/perfil.actualizado.schema.json` — validado con
`jsonschema` en `tests/test_bus_eventos.py`. Eso es lo que importa: cuando el
bus real llegue, el payload ya es correcto.

Nota sobre logs (regla de arquitectura #6, sin datos personales en logs): el
evento en sí lleva `clienteId` en claro porque el contrato así lo exige (es
el identificador que RATING necesita para correlacionar, no un dato personal
en el sentido de nombre/documento/cuenta). Pero el PLACEHOLDER de este bus es,
literalmente, una línea de log — así que para no filtrar `clienteId` por esa
vía mientras no hay un bus real, lo que se imprime es una versión redactada
(con el mismo hash opaco que el resto del servicio), no el evento completo.
El evento devuelto por `construir_evento_perfil_actualizado` (y el que se le
pasa a `publicar`) sí lleva el `clienteId` real, tal como lo exige el
contrato; lo único redactado es la línea de log de este placeholder."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("risk.eventos")


def construir_evento_perfil_actualizado(
    cliente_id: str, perfil_version: int, operation_time: str
) -> dict[str, Any]:
    """Construye el payload de `perfil.actualizado` (contrato v1)."""
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "perfil.actualizado",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": {
            "clienteId": cliente_id,
            "perfilVersion": perfil_version,
            "operationTime": operation_time,
        },
    }


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> None: ...


class BusEventosLog:
    """Adaptador PLACEHOLDER: loguea una versión redactada del evento en vez
    de publicarlo en Pub/Sub (que todavía no existe)."""

    def publicar(self, evento: dict[str, Any]) -> None:
        datos = evento["datos"]
        logger.info(
            "evento=%s eventId=%s cliente=%s perfilVersion=%s operationTime=%s "
            "[PLACEHOLDER: sin Pub/Sub real todavia]",
            evento["tipo"],
            evento["eventId"],
            id_opaco(datos["clienteId"]),
            datos["perfilVersion"],
            datos["operationTime"],
        )
