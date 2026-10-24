"""Caso de uso: verificar la identidad del cliente con el proveedor de KYC, lado ACL (HU-W03).

Si el proveedor falla o el circuito está abierto, la verificación queda `pendiente` (nunca un 5xx
crudo, regla #4): quien decide la suscripción manda la propuesta a revisión manual en vez de
aprobarla a ciegas o tumbar el flujo. El proveedor es idempotente por `id_verificacion`, así que
repetir tras un corte por tiempo devuelve el mismo resultado.

El ACL Worker no guarda el resultado: lo usa UNDER para decidir."""

import logging
from typing import Protocol

import purgatory

from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.application.reintento import con_reintento
from app.domain.puertos import ResultadoKyc, SolicitudKyc

logger = logging.getLogger("acl_worker.kyc")

MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"
MOTIVO_KYC_NO_DISPONIBLE = "kyc_no_disponible"


class PuertoKycConTimeout(Protocol):
    def verificar(
        self, solicitud: SolicitudKyc, timeout_s: float | None = None
    ) -> ResultadoKyc: ...


class ServicioKyc:
    def __init__(
        self,
        adaptador: PuertoKycConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        presupuesto_s: float,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._presupuesto_s = presupuesto_s

    def verificar(self, solicitud: SolicitudKyc) -> ResultadoKyc:
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                return con_reintento(
                    lambda t: self._adaptador.verificar(solicitud, timeout_s=t),
                    self._presupuesto_s,
                )
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, KYC pendiente id=%s",
                self._nombre_circuito,
                id_opaco(solicitud.id_verificacion),
            )
            return ResultadoKyc(estado="pendiente", motivo=MOTIVO_CIRCUITO_ABIERTO)
        except Exception as error:
            logger.warning(
                "fallo verificando circuito=%s id=%s error=%s",
                self._nombre_circuito,
                id_opaco(solicitud.id_verificacion),
                type(error).__name__,
            )
        return ResultadoKyc(estado="pendiente", motivo=MOTIVO_KYC_NO_DISPONIBLE)
