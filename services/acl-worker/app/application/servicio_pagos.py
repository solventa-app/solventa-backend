"""Caso de uso: cobrar con la pasarela, lado ACL (T-W05-2).

Si la pasarela falla o el circuito está abierto, el cobro queda `pendiente`
(nunca un 5xx crudo — CA-W05-04). El `motivo` distingue los dos casos
(`circuito_abierto` vs. `pasarela_no_disponible`): con el circuito abierto ni
siquiera se intentó, y PAYMENTS no le cuenta ese intento (KAN-28).

`consultar` es la conciliación (KAN-28): pregunta a la pasarela, con el mismo
Circuit Breaker y el mismo presupuesto de 700 ms, si un cobro ya existe.

El reintento del cobro pendiente vía Cloud Tasks (T-W05-4) y la idempotencia
persistente (T-W05-3) son responsabilidad de PAYMENTS, no de este servicio: el
ACL Worker no guarda estado de cobros.
"""

import logging
from typing import Protocol

import purgatory

from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.application.reintento import con_reintento
from app.domain.puertos import ConsultaCobro, OrdenCobro, ResultadoCobro

logger = logging.getLogger("acl_worker.pagos")


MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"
MOTIVO_PASARELA_NO_DISPONIBLE = "pasarela_no_disponible"


class PuertoPasarelaConTimeout(Protocol):
    def cobrar(self, orden: OrdenCobro, timeout_s: float | None = None) -> ResultadoCobro: ...

    def consultar(
        self, referencia_comercio: str, timeout_s: float | None = None
    ) -> ResultadoCobro | None: ...


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
            # Regla #6: una póliza identifica, indirectamente, a una persona.
            logger.warning(
                "circuito=%s abierto, cobro pendiente poliza=%s",
                self._nombre_circuito,
                id_opaco(orden.poliza_id),
            )
            return ResultadoCobro(estado="pendiente", motivo=MOTIVO_CIRCUITO_ABIERTO)
        except Exception as error:
            logger.warning(
                "fallo cobrando circuito=%s poliza=%s error=%s",
                self._nombre_circuito,
                id_opaco(orden.poliza_id),
                type(error).__name__,
            )

        return ResultadoCobro(estado="pendiente", motivo=MOTIVO_PASARELA_NO_DISPONIBLE)

    def consultar(self, referencia_comercio: str) -> ConsultaCobro:
        """Conciliación: `encontrado`, `no_encontrado` o `desconocido` (no se pudo
        preguntar). Nunca lanza: quien llama decide qué hacer con un `desconocido`."""
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                cobro = con_reintento(
                    lambda t: self._adaptador.consultar(referencia_comercio, timeout_s=t),
                    self._presupuesto_s,
                )
        except CircuitoAbiertoError:
            logger.warning("circuito=%s abierto, conciliación sin respuesta", self._nombre_circuito)
            return ConsultaCobro(resultado="desconocido")
        except Exception as error:
            logger.warning(
                "fallo consultando circuito=%s referencia=%s error=%s",
                self._nombre_circuito,
                id_opaco(referencia_comercio),
                type(error).__name__,
            )
            return ConsultaCobro(resultado="desconocido")
        if cobro is None:
            return ConsultaCobro(resultado="no_encontrado")
        return ConsultaCobro(resultado="encontrado", cobro=cobro)
