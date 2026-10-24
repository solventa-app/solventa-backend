"""Caso de uso: enviar un aviso al cliente con el proveedor de notificaciones, lado ACL (HU-W03).

Si el proveedor falla o el circuito está abierto, el aviso queda `pendiente` (nunca un 5xx crudo,
regla #4): POLICY lo reintenta, porque la entrega del documento es una obligación con el
asegurado. El proveedor es idempotente por `id_notificacion`, así que reintentar tras un corte por
tiempo no manda dos avisos.

El ACL Worker no guarda el aviso: POLICY registra que ya lo envió."""

import logging
from typing import Protocol

import purgatory

from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.application.reintento import con_reintento
from app.domain.puertos import ResultadoNotificacion, SolicitudNotificacion

logger = logging.getLogger("acl_worker.notificaciones")

MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"
MOTIVO_NOTIFICACIONES_NO_DISPONIBLES = "notificaciones_no_disponibles"


class PuertoNotificacionesConTimeout(Protocol):
    def enviar(
        self, solicitud: SolicitudNotificacion, timeout_s: float | None = None
    ) -> ResultadoNotificacion: ...


class ServicioNotificaciones:
    def __init__(
        self,
        adaptador: PuertoNotificacionesConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        presupuesto_s: float,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._presupuesto_s = presupuesto_s

    def enviar(self, solicitud: SolicitudNotificacion) -> ResultadoNotificacion:
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                return con_reintento(
                    lambda t: self._adaptador.enviar(solicitud, timeout_s=t), self._presupuesto_s
                )
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, aviso pendiente id=%s",
                self._nombre_circuito,
                id_opaco(solicitud.id_notificacion),
            )
            return ResultadoNotificacion(estado="pendiente", motivo=MOTIVO_CIRCUITO_ABIERTO)
        except Exception as error:
            logger.warning(
                "fallo enviando circuito=%s id=%s error=%s",
                self._nombre_circuito,
                id_opaco(solicitud.id_notificacion),
                type(error).__name__,
            )
        return ResultadoNotificacion(
            estado="pendiente", motivo=MOTIVO_NOTIFICACIONES_NO_DISPONIBLES
        )
