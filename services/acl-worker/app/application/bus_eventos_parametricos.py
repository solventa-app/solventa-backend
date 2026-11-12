"""Publicador del tema `eventos-parametricos` (KAN-259, Fase 3). Mismo patrón que
`risk/app/application/bus_eventos.py` y `enviar-push/app/bus_eventos.py`: `BusEventosPubSub` si
`PUBSUB_TEMA_EVENTOS_PARAMETRICOS` está configurado, si no `BusEventosLog` (placeholder de
local/CI, sin Pub/Sub real todavía). Nadie consume este tema aún (fase 4, CLAIMS, queda fuera de
esta tarea): esta pieza solo deja al ACL Worker listo para publicar."""

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger("acl_worker.eventos_parametricos.bus")


class PuertoBusEventosParametricos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> None: ...


class BusEventosLog:
    def publicar(self, evento: dict[str, Any]) -> None:
        datos = evento["datos"]
        logger.info(
            "evento=%s eventId=%s tipo=%s clave=%s valor=%s [PLACEHOLDER: sin Pub/Sub real]",
            evento["tipo"],
            evento["eventId"],
            datos["tipo"],
            datos["clave"],
            datos["valor"],
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
        datos = evento["datos"]
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
                "evento=%s eventId=%s tipo=%s clave=%s valor=%s tema=%s [PUBSUB]",
                evento["tipo"],
                evento["eventId"],
                datos["tipo"],
                datos["clave"],
                datos["valor"],
                self._tema_ruta,
            )
        except Exception as error:
            logger.warning(
                "fallo publicando en Pub/Sub tema=%s error=%s; se degrada a BusEventosLog",
                self._tema_ruta,
                error,
            )
            self._fallback.publicar(evento)


def construir_bus_eventos_parametricos(
    tema_ruta: str | None, cliente_pubsub: Any = None
) -> PuertoBusEventosParametricos:
    if tema_ruta:
        return BusEventosPubSub(tema_ruta, cliente_pubsub=cliente_pubsub)
    return BusEventosLog()
