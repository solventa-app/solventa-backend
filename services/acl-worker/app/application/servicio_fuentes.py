"""Caso de uso: consultar una fuente (Open Finance u Open Data) orquestando
adaptador + Circuit Breaker + retry + degradación con caché (T-W01-8/9/10).

Nunca deja escapar una excepción cruda: ante cualquier falla (circuito
abierto, timeout, HTTP 5xx del proveedor) degrada al último valor cacheado
(`de_cache=True`) o, si no hay nada en caché, a un valor por defecto mínimo
(`degradado=True`) — regla de arquitectura #4. La composición de oferta/score
a partir de este dato es trabajo de RISK/RATING, no de este servicio.
"""

import logging
from datetime import UTC, datetime
from typing import Protocol

import purgatory

from app.application.cache import CacheFuentes
from app.application.circuitos import CircuitoAbiertoError
from app.application.identificadores import id_opaco
from app.application.reconciliacion import ProductorReconciliacion, ProductorReconciliacionNulo
from app.application.reintento import con_reintento
from app.domain.puertos import ConsultaFuente, DatosFuente

logger = logging.getLogger("acl_worker.fuentes")


class PuertoFuenteConTimeout(Protocol):
    def consultar(
        self, consulta: ConsultaFuente, timeout_s: float | None = None
    ) -> DatosFuente: ...


class ServicioConsultaFuentes:
    def __init__(
        self,
        adaptador: PuertoFuenteConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito: str,
        cache: CacheFuentes,
        presupuesto_s: float,
        productor_reconciliacion: ProductorReconciliacion | ProductorReconciliacionNulo
        | None = None,
    ) -> None:
        self._adaptador = adaptador
        self._fabrica = fabrica_circuitos
        self._nombre_circuito = nombre_circuito
        self._cache = cache
        self._presupuesto_s = presupuesto_s
        self._productor_reconciliacion = productor_reconciliacion or ProductorReconciliacionNulo()

    def consultar(self, consulta: ConsultaFuente) -> DatosFuente:
        cliente_opaco = id_opaco(consulta.cliente_id)
        try:
            with self._fabrica.get_breaker(self._nombre_circuito):
                datos = con_reintento(
                    lambda t: self._adaptador.consultar(consulta, timeout_s=t),
                    self._presupuesto_s,
                )
            self._cache.guardar(consulta.fuente, consulta.cliente_id, datos)
            return datos
        except CircuitoAbiertoError:
            logger.warning(
                "circuito=%s abierto, fail-fast fuente=%s cliente=%s",
                self._nombre_circuito,
                consulta.fuente,
                cliente_opaco,
            )
        except Exception as error:
            logger.warning(
                "fallo consultando circuito=%s fuente=%s cliente=%s error=%s",
                self._nombre_circuito,
                consulta.fuente,
                cliente_opaco,
                type(error).__name__,
            )

        # No se consiguió la respuesta real en vivo (circuito abierto o falla):
        # se encola (fire-and-forget) una reconciliación diferida, sin importar
        # si la respuesta a este llamador termina siendo `de_cache` o
        # `degradado` (contrato del Consolidador, ver reconciliacion.py).
        self._productor_reconciliacion.encolar_si_corresponde(
            consulta.fuente, consulta.cliente_id, consulta.consentimiento_id
        )

        cacheado = self._cache.obtener(consulta.fuente, consulta.cliente_id)
        if cacheado is not None:
            cacheado.de_cache = True
            return cacheado

        return DatosFuente(
            fuente=consulta.fuente,
            datos={},
            capturado_en=datetime.now(UTC),
            degradado=True,
        )
