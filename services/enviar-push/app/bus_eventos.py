"""Publicador del tema `notificaciones-push` (KAN-259, D2: Pub/Sub real aprobado para este tema).
Mismo patrón que `risk/app/application/bus_eventos.py`: `BusEventosPubSub` si
`PUBSUB_TEMA_NOTIFICACIONES_PUSH` está configurado, si no `BusEventosLog` (placeholder de local/CI).
Solo lo usa la puerta HTTP (`POST /v1/push`); un productor que prefiere evitar el salto HTTP puede
publicar directo en el tema con este mismo payload."""

import json
import logging
from typing import Any, Protocol

from app.identificadores import id_opaco

logger = logging.getLogger("enviar_push.bus")


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> None: ...


class BusEventosLog:
    def publicar(self, evento: dict[str, Any]) -> None:
        logger.info(
            "evento=%s eventId=%s usuario=%s plantilla=%s [PLACEHOLDER: sin Pub/Sub real todavia]",
            evento["tipo"],
            evento["eventId"],
            id_opaco(evento["datos"]["usuarioId"]),
            evento["datos"]["plantilla"],
        )


class BusEventosPubSub:
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
        cuerpo = json.dumps(evento).encode("utf-8")
        try:
            cliente = self._obtener_cliente()
            try:
                futuro = cliente.publish(self._tema_ruta, cuerpo, tipo=str(evento["tipo"]))
            except TypeError:
                futuro = cliente.publish(self._tema_ruta, cuerpo)
            if hasattr(futuro, "result"):
                futuro.result(timeout=5.0)
            logger.info(
                "evento=%s eventId=%s usuario=%s plantilla=%s tema=%s [PUBSUB]",
                evento["tipo"],
                evento["eventId"],
                id_opaco(evento["datos"]["usuarioId"]),
                evento["datos"]["plantilla"],
                self._tema_ruta,
            )
        except Exception as error:
            logger.warning(
                "fallo publicando en Pub/Sub tema=%s error=%s; se degrada a BusEventosLog",
                self._tema_ruta,
                error,
            )
            self._fallback.publicar(evento)


def construir_bus_eventos(tema_ruta: str | None, cliente_pubsub: Any = None) -> PuertoBusEventos:
    if tema_ruta:
        return BusEventosPubSub(tema_ruta, cliente_pubsub=cliente_pubsub)
    return BusEventosLog()
