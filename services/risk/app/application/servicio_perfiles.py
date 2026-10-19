"""Caso de uso: registrar un perfil de riesgo (T-W01-6/7, HU-W01).

RISK recibe `cliente_id` y los datos ya resueltos de las fuentes (el shape
que ya devuelve `acl-worker` en `POST /fuentes/consultar`: `fuente`, `datos`,
`capturado_en`, `de_cache`, `degradado` — no se inventa un contrato nuevo con
el ACL Worker, se reusa ese shape tal cual llega en el cuerpo de la
petición), persiste el perfil versionado y devuelve el `operationTime` de la
escritura (D-02) junto con la versión asignada. Emite `perfil.actualizado`
por el bus de eventos (placeholder, ver `bus_eventos.py`).

Depende de `RepositorioPerfiles` (`Protocol` estructural, no un puerto
hexagonal formal) para poder probarse con un doble sin levantar MongoDB."""

import logging
from decimal import Decimal
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_perfil_actualizado
from app.application.identificadores import id_opaco
from app.application.motor_decision import PoliticaCredito, ResultadoDecision, decidir_preaprobacion
from app.application.motor_scoring import (
    calcular_score_motor,
    extraer_perfil_desde_fuentes,
)
from app.application.tiempo_causal import ComponentesTiempo, serializar_operation_time

logger = logging.getLogger("risk.perfiles")


class RepositorioPerfiles(Protocol):
    def guardar(
        self,
        cliente_id: str,
        fuentes: list[dict[str, Any]],
        evaluacion: dict[str, Any] | None = None,
    ) -> tuple[int, ComponentesTiempo]: ...


class ServicioPerfiles:
    def __init__(self, repositorio: RepositorioPerfiles, bus: PuertoBusEventos) -> None:
        self._repositorio = repositorio
        self._bus = bus

    def registrar(self, cliente_id: str, fuentes: list[dict[str, Any]]) -> dict[str, Any]:
        # Evaluación con motor de scoring
        perfil_entrada = extraer_perfil_desde_fuentes(fuentes)
        resultado_score = calcular_score_motor(perfil_entrada)

        evaluacion = {
            "score": resultado_score.score,
            "score_normalizado": resultado_score.score_normalizado,
            "clasificacion": resultado_score.clasificacion,
            "nivel_riesgo": resultado_score.nivel_riesgo,
            "es_preliminar": resultado_score.es_preliminar,
            "variables": resultado_score.variables,
            "razones": [r.model_dump() for r in resultado_score.razones],
            "desglose": {k: v.model_dump() for k, v in resultado_score.desglose_por_factor.items()},
        }

        try:
            perfil_version, operation_time = self._repositorio.guardar(
                cliente_id, fuentes, evaluacion=evaluacion
            )
        except TypeError:
            perfil_version, operation_time = self._repositorio.guardar(cliente_id, fuentes)

        operation_time_str = serializar_operation_time(operation_time)

        logger.info(
            "perfil registrado cliente=%s perfil_version=%s score=%d clasificacion=%s",
            id_opaco(cliente_id),
            perfil_version,
            resultado_score.score,
            resultado_score.clasificacion,
        )

        evento = construir_evento_perfil_actualizado(cliente_id, perfil_version, operation_time_str)
        self._bus.publicar(evento)

        return {
            "cliente_id": cliente_id,
            "perfil_version": perfil_version,
            "operation_time": operation_time_str,
            "score": resultado_score.score,
            "clasificacion": resultado_score.clasificacion,
        }

    def evaluar_preaprobacion(
        self,
        cliente_id: str,
        monto: Decimal,
        plazo_meses: int,
        fuentes: list[dict[str, Any]],
        politica: PoliticaCredito | None = None,
    ) -> ResultadoDecision:
        """Evalúa las reglas de decisión de preaprobación para un cliente con sus fuentes."""
        perfil_entrada = extraer_perfil_desde_fuentes(fuentes)
        return decidir_preaprobacion(perfil_entrada, monto, plazo_meses, politica)
