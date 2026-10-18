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

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("risk.eventos")


def construir_evento_perfil_actualizado(
    cliente_id: str,
    perfil_version: int,
    operation_time: str,
    nivel_riesgo: str | None = None,
    fecha_vigencia: str | None = None,
    version_reglas: str | None = None,
) -> dict[str, Any]:
    """Construye el payload de `perfil.actualizado` (contrato v1)."""
    datos: dict[str, Any] = {
        "clienteId": cliente_id,
        "perfilVersion": perfil_version,
        "operationTime": operation_time,
    }
    if nivel_riesgo is not None:
        datos["nivelRiesgo"] = nivel_riesgo
    if fecha_vigencia is not None:
        datos["fechaVigencia"] = fecha_vigencia
    if version_reglas is not None:
        datos["versionReglas"] = version_reglas

    return {
        "eventId": str(uuid.uuid4()),
        "tipo": "perfil.actualizado",
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": datos,
    }


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> None: ...


class BusEventosLog:
    """Adaptador PLACEHOLDER/local: loguea una versión redactada del evento
    cuando `PUBSUB_TEMA_PERFIL_ACTUALIZADO` no está configurado."""

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


class BusEventosPubSub:
    """Publicador real sobre Google Cloud Pub/Sub para `perfil.actualizado`
    (Bloque 8, decisión 9). Si Pub/Sub falla, degrada a `BusEventosLog` para no
    tumbar la escritura del perfil (regla de arquitectura #4)."""

    def __init__(self, tema_ruta: str, cliente_pubsub: Any = None) -> None:
        self._tema_ruta = tema_ruta
        self._cliente = cliente_pubsub
        self._fallback = BusEventosLog()

    def _obtener_cliente(self) -> Any:
        if self._cliente is None:
            from google.cloud import pubsub_v1  # type: ignore[import-untyped]

            self._cliente = pubsub_v1.PublisherClient()
        return self._cliente

    def publicar(self, evento: dict[str, Any]) -> None:
        datos = evento["datos"]
        cuerpo = json.dumps(evento).encode("utf-8")
        try:
            cliente = self._obtener_cliente()
            try:
                futuro = cliente.publish(
                    self._tema_ruta,
                    cuerpo,
                    tipo=str(evento["tipo"]),
                    version=str(evento["version"]),
                )
            except TypeError:
                futuro = cliente.publish(self._tema_ruta, cuerpo)
            if hasattr(futuro, "result"):
                futuro.result(timeout=5.0)
            logger.info(
                "evento=%s eventId=%s cliente=%s perfilVersion=%s operationTime=%s "
                "tema=%s [PUBSUB]",
                evento["tipo"],
                evento["eventId"],
                id_opaco(datos["clienteId"]),
                datos["perfilVersion"],
                datos["operationTime"],
                self._tema_ruta,
            )
        except Exception as error:
            logger.warning(
                "fallo publicando en Pub/Sub tema=%s cliente=%s error=%s; "
                "se degrada a BusEventosLog",
                self._tema_ruta,
                id_opaco(datos["clienteId"]),
                error,
            )
            self._fallback.publicar(evento)


def construir_bus_eventos(
    tema_ruta: str | None = None, cliente_pubsub: Any = None
) -> PuertoBusEventos:
    """Devuelve `BusEventosPubSub` si `PUBSUB_TEMA_PERFIL_ACTUALIZADO` está
    definido; si no, `BusEventosLog`."""
    tema = (
        tema_ruta
        if tema_ruta is not None
        else os.getenv("PUBSUB_TEMA_PERFIL_ACTUALIZADO", "").strip()
    )
    if tema:
        return BusEventosPubSub(tema, cliente_pubsub=cliente_pubsub)
    return BusEventosLog()
