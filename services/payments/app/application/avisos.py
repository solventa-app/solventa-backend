"""Avisos de cobro para la tabla del back-office (W05-A6).

Envuelve al bus de eventos, como `BusConReintento`: cada cambio de estado de un cobro se publica en
`avisos:cobros`, y el servicio `avisos` lo reenvía por SSE. Sin datos personales. Es de mejor
esfuerzo: un Redis caído nunca afecta al cobro, y al reabrir la pantalla se consulta el estado
real."""

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger("payments.avisos")

CANAL_COBROS = "avisos:cobros"


class PuertoRedis(Protocol):
    def publish(self, canal: str, mensaje: str) -> object: ...


def redis_de_avisos(url: str) -> PuertoRedis:
    import redis  # solo si hay Redis configurado

    return redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)


class BusConAvisos:
    def __init__(self, bus: Any, cliente: PuertoRedis) -> None:
        self._bus = bus
        self._cliente = cliente

    def publicar(self, evento: dict[str, Any]) -> None:
        self._bus.publicar(evento)
        datos = evento["datos"]
        cuerpo = json.dumps(
            {
                "tipo": "cobro",
                "cobroId": datos["cobroId"],
                "polizaId": datos["polizaId"],
                "estado": datos["estado"].upper(),
            }
        )
        try:
            self._cliente.publish(CANAL_COBROS, cuerpo)
        except Exception as error:
            logger.warning("no se pudo publicar el aviso error=%s", type(error).__name__)
