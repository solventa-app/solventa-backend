"""Bus de eventos de PAYMENTS — PLACEHOLDER.

No existe infraestructura de Pub/Sub todavía (sin proyecto GCP, ver
`CLAUDE.md`). Esta es una interfaz mínima (función/clase, sin puertos y
adaptadores formales — regla de arquitectura #3: hexagonal es solo para
`acl-worker`) con un adaptador que por ahora solo loguea el evento ya
serializado. Cuando `infra-engineer` levante Pub/Sub real, se sustituye
`BusEventosLog` por un publicador real detrás del mismo `PuertoBusEventos`,
sin tocar `ServicioCobros` ni `app.application.reintento`.

El evento que se publica (`evento_cobro_estado_cambiado`) cumple exactamente
el esquema de `contracts/events/cobro.estado-cambiado.schema.json` —
validado con `jsonschema` en `tests/test_bus_eventos.py`.

Nota sobre logs (regla de arquitectura #6, sin datos personales en logs), a
diferencia de `services/risk/app/application/bus_eventos.py`: aquí el
payload de `cobro.estado-cambiado` no tiene ningún dato personal propiamente
dicho — `cobroId` es un identificador interno (UUID generado por PAYMENTS,
no identifica a nadie por sí mismo) y `claveIdempotencia` es un valor opaco
de correlación que el cliente genera, así que ambos se loguean en claro.
`polizaId` sí identifica, indirectamente, a la persona asegurada, así que se
redacta con el mismo hash opaco que usan RISK y el ACL Worker
(`identificadores.id_opaco`) — más estricto que el logging actual del ACL
Worker (`app/application/servicio_pagos.py`, que loguea `poliza_id` en
claro); no se toca ese servicio aquí, solo se documenta la diferencia."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("payments.eventos")


def construir_evento_cobro_estado_cambiado(
    cobro_id: str, poliza_id: str, estado: str, clave_idempotencia: str
) -> dict[str, Any]:
    """Construye el payload de `cobro.estado-cambiado` (contrato v1)."""
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "cobro.estado-cambiado",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": {
            "cobroId": cobro_id,
            "polizaId": poliza_id,
            "estado": estado,
            "claveIdempotencia": clave_idempotencia,
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
            "evento=%s eventId=%s cobroId=%s poliza=%s estado=%s claveIdempotencia=%s "
            "[PLACEHOLDER: sin Pub/Sub real todavia]",
            evento["tipo"],
            evento["eventId"],
            datos["cobroId"],
            id_opaco(datos["polizaId"]),
            datos["estado"],
            datos["claveIdempotencia"],
        )
