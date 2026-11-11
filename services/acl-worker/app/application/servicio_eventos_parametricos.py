"""Caso de uso: consultar una fuente paramétrica (clima o vuelos) y, si hay dato, publicar
`evento.parametrico.recibido` (HU-M04, KAN-259, Fase 3).

Lo dispara `POST /eventos-parametricos/_consultar`, que en producción llama un Cloud Scheduler
(fase de infra posterior, fuera de esta tarea): no es parte del camino síncrono de un usuario, así
que nunca hay un cliente esperando. Aun así nunca propaga un 5xx crudo (regla #4): si el proveedor
falla o el circuito está abierto, no hay evento que publicar y el próximo tick del scheduler
reintenta sin daño (no hay un siniestro paramétrico que dependa de este tick en particular)."""

import logging
from typing import Any, Protocol

import purgatory

from app.application.bus_eventos_parametricos import PuertoBusEventosParametricos
from app.application.circuitos import CircuitoAbiertoError
from app.application.esquema_evento_parametrico import (
    construir_evento_clima,
    construir_evento_vuelo,
    validar,
)
from app.application.reintento import con_reintento
from app.domain.puertos import ConsultaClima, ConsultaVuelo, MedicionClima, MedicionVuelo

logger = logging.getLogger("acl_worker.eventos_parametricos")

MOTIVO_CIRCUITO_ABIERTO = "circuito_abierto"
MOTIVO_PROVEEDOR_NO_DISPONIBLE = "proveedor_no_disponible"
MOTIVO_VUELO_NO_ENCONTRADO = "vuelo_no_encontrado"


class PuertoClimaConTimeout(Protocol):
    def consultar(
        self, consulta: ConsultaClima, timeout_s: float | None = None
    ) -> MedicionClima: ...


class PuertoVueloConTimeout(Protocol):
    def consultar(
        self, consulta: ConsultaVuelo, timeout_s: float | None = None
    ) -> MedicionVuelo: ...


class ServicioEventosParametricos:
    def __init__(
        self,
        adaptador_clima: PuertoClimaConTimeout,
        adaptador_vuelo: PuertoVueloConTimeout,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        nombre_circuito_clima: str,
        nombre_circuito_vuelo: str,
        presupuesto_clima_s: float,
        presupuesto_vuelo_s: float,
        bus_eventos: PuertoBusEventosParametricos,
    ) -> None:
        self._clima = adaptador_clima
        self._vuelo = adaptador_vuelo
        self._fabrica = fabrica_circuitos
        self._circuito_clima = nombre_circuito_clima
        self._circuito_vuelo = nombre_circuito_vuelo
        self._presupuesto_clima_s = presupuesto_clima_s
        self._presupuesto_vuelo_s = presupuesto_vuelo_s
        self._bus = bus_eventos

    def consultar_clima(self, consulta: ConsultaClima) -> tuple[dict[str, Any] | None, str | None]:
        """`(evento, None)` si se publicó; `(None, motivo)` si no hay dato que publicar."""
        try:
            with self._fabrica.get_breaker(self._circuito_clima):
                medicion = con_reintento(
                    lambda t: self._clima.consultar(consulta, timeout_s=t),
                    self._presupuesto_clima_s,
                )
        except CircuitoAbiertoError:
            logger.warning("circuito=%s abierto, sin evento de clima", self._circuito_clima)
            return None, MOTIVO_CIRCUITO_ABIERTO
        except Exception as error:
            logger.warning(
                "fallo consultando clima circuito=%s error=%s",
                self._circuito_clima,
                type(error).__name__,
            )
            return None, MOTIVO_PROVEEDOR_NO_DISPONIBLE
        evento = construir_evento_clima(
            clave=consulta.clave,
            variable=consulta.variable,
            valor=medicion.valor,
            medido_en=medicion.medido_en,
        )
        validar(evento)
        self._bus.publicar(evento)
        return evento, None

    def consultar_vuelo(self, consulta: ConsultaVuelo) -> tuple[dict[str, Any] | None, str | None]:
        """`(evento, None)` si se publicó; `(None, motivo)` si no hay dato que publicar."""
        try:
            with self._fabrica.get_breaker(self._circuito_vuelo):
                medicion = con_reintento(
                    lambda t: self._vuelo.consultar(consulta, timeout_s=t),
                    self._presupuesto_vuelo_s,
                )
        except CircuitoAbiertoError:
            logger.warning("circuito=%s abierto, sin evento de vuelo", self._circuito_vuelo)
            return None, MOTIVO_CIRCUITO_ABIERTO
        except Exception as error:
            logger.warning(
                "fallo consultando vuelo circuito=%s error=%s",
                self._circuito_vuelo,
                type(error).__name__,
            )
            return None, MOTIVO_PROVEEDOR_NO_DISPONIBLE
        if not medicion.encontrado:
            return None, MOTIVO_VUELO_NO_ENCONTRADO
        evento = construir_evento_vuelo(
            numero_vuelo=consulta.numero_vuelo,
            fecha=consulta.fecha,
            retraso_minutos=medicion.retraso_minutos,
            cancelado=medicion.cancelado,
            medido_en=medicion.medido_en or "",
        )
        validar(evento)
        self._bus.publicar(evento)
        return evento, None
