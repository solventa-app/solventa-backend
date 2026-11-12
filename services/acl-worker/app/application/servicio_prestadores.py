"""Caso de uso: pedir a la red de prestadores que atienda una asistencia en sitio (HU-M06, v0.49).

La llama CLAIMS. Siempre responde: si el proveedor falla o el circuito está abierto, la asistencia
queda `NO_CONFIRMADA` con `PRESTADOR_SIN_RESPUESTA` (regla 4, nunca un 5xx crudo) y la app ofrece
reintentar con el mismo id. Ni la ubicación ni el id del cliente van a los logs.
"""

import logging
from typing import Protocol

import purgatory

from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.application.reintento import con_reintento
from app.domain.puertos import DesenlaceAsistencia, PedidoAsistencia

logger = logging.getLogger("acl_worker.prestadores")

MOTIVO_SIN_RESPUESTA = "PRESTADOR_SIN_RESPUESTA"


class PuertoPrestadoresConTimeout(Protocol):
    def solicitar(
        self, pedido: PedidoAsistencia, timeout_s: float | None = None
    ) -> DesenlaceAsistencia: ...


class ServicioPrestadores:
    def __init__(
        self,
        adaptador: PuertoPrestadoresConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        presupuesto_s: float,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._presupuesto_s = presupuesto_s

    def solicitar(self, pedido: PedidoAsistencia) -> DesenlaceAsistencia:
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                return con_reintento(
                    lambda t: self._adaptador.solicitar(pedido, timeout_s=t),
                    self._presupuesto_s,
                )
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, asistencia sin confirmar id=%s",
                self._nombre_circuito,
                id_opaco(pedido.asistencia_id),
            )
        except Exception as error:
            logger.warning(
                "fallo pidiendo la asistencia circuito=%s id=%s error=%s",
                self._nombre_circuito,
                id_opaco(pedido.asistencia_id),
                type(error).__name__,
            )
        return DesenlaceAsistencia(estado="NO_CONFIRMADA", motivo=MOTIVO_SIN_RESPUESTA)
