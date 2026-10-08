"""Caso de uso: cobrar con la pasarela, lado ACL (T-W05-2).

Si la pasarela falla o el circuito está abierto, el cobro queda `pendiente`
(nunca un 5xx crudo — CA-W05-04). El reintento del cobro pendiente vía Cloud
Tasks (T-W05-4) y la idempotencia persistente (T-W05-3) son responsabilidad de
PAYMENTS, no de este servicio: el ACL Worker no guarda estado de cobros.
"""

import logging
from typing import Protocol

import purgatory

from app.application.circuitos import CircuitoAbiertoError
from app.application.reintento import con_reintento
from app.domain.puertos import OrdenCobro, ResultadoCobro

logger = logging.getLogger("acl_worker.pagos")


class PuertoPasarelaConTimeout(Protocol):
    def cobrar(self, orden: OrdenCobro, timeout_s: float | None = None) -> ResultadoCobro: ...


class ServicioCobro:
    def __init__(
        self,
        adaptador: PuertoPasarelaConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        presupuesto_s: float,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._presupuesto_s = presupuesto_s

    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro:
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                return con_reintento(
                    lambda t: self._adaptador.cobrar(orden, timeout_s=t),
                    self._presupuesto_s,
                )
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, cobro pendiente poliza=%s",
                self._nombre_circuito,
                orden.poliza_id,
            )
        except Exception as error:
            logger.warning(
                "fallo cobrando circuito=%s poliza=%s error=%s",
                self._nombre_circuito,
                orden.poliza_id,
                type(error).__name__,
            )

        return ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")
