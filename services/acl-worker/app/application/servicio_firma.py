"""Caso de uso: firmar el documento de la póliza con el proveedor, lado ACL (HU-W03, CA-W03-03).

Si el proveedor falla o el circuito está abierto, la firma queda `pendiente` (nunca un 5xx crudo,
regla #4): POLICY la ofrece reintentar. Como el proveedor es idempotente por `id_firma`, reintentar
tras un corte por tiempo devuelve la misma evidencia en vez de firmar dos veces.

El ACL Worker no guarda la evidencia: la guarda POLICY (escritor único de las firmas)."""

import logging
from typing import Protocol

import purgatory

from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.application.reintento import con_reintento
from app.domain.puertos import ResultadoFirma, SolicitudFirma

logger = logging.getLogger("acl_worker.firma")

MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"
MOTIVO_FIRMA_NO_DISPONIBLE = "firma_no_disponible"


class PuertoFirmaConTimeout(Protocol):
    def firmar(
        self, solicitud: SolicitudFirma, timeout_s: float | None = None
    ) -> ResultadoFirma: ...


class ServicioFirma:
    def __init__(
        self,
        adaptador: PuertoFirmaConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        presupuesto_s: float,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._presupuesto_s = presupuesto_s

    def firmar(self, solicitud: SolicitudFirma) -> ResultadoFirma:
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                return con_reintento(
                    lambda t: self._adaptador.firmar(solicitud, timeout_s=t), self._presupuesto_s
                )
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, firma pendiente id=%s",
                self._nombre_circuito,
                id_opaco(solicitud.id_firma),
            )
            return ResultadoFirma(estado="pendiente", motivo=MOTIVO_CIRCUITO_ABIERTO)
        except Exception as error:
            logger.warning(
                "fallo firmando circuito=%s id=%s error=%s",
                self._nombre_circuito,
                id_opaco(solicitud.id_firma),
                type(error).__name__,
            )
        return ResultadoFirma(estado="pendiente", motivo=MOTIVO_FIRMA_NO_DISPONIBLE)
