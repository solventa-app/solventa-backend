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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_perfil_actualizado
from app.application.cache_score import CacheScore
from app.application.calculo_score import calcular_resultado_score
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
        **kwargs: Any,
    ) -> tuple[int, ComponentesTiempo]: ...

    def borrar_de_consentimiento(
        self, cliente_id: str, consentimiento_id: str
    ) -> list[dict[str, Any]]: ...

    def borrar_vencidos(self, ahora: datetime) -> list[dict[str, Any]]: ...


class ServicioPerfiles:
    def __init__(
        self,
        repositorio: RepositorioPerfiles,
        bus: PuertoBusEventos,
        cache_score: CacheScore | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._bus = bus
        self._cache_score = cache_score

    def registrar(
        self,
        cliente_id: str,
        fuentes: list[dict[str, Any]],
        consentimiento_id: str | None = None,
        consentimiento_vence_en: datetime | None = None,
    ) -> dict[str, Any]:
        resultado_score = calcular_resultado_score(fuentes)

        # Evaluación con motor de scoring (HU-W02): se guarda completa en el perfil.
        # El score de la caché y del evento sigue siendo el de `calcular_resultado_score`.
        perfil_entrada = extraer_perfil_desde_fuentes(fuentes)
        resultado_motor = calcular_score_motor(perfil_entrada)

        evaluacion = {
            "score": resultado_motor.score,
            "score_normalizado": resultado_motor.score_normalizado,
            "clasificacion": resultado_motor.clasificacion,
            "nivel_riesgo": resultado_motor.nivel_riesgo,
            "es_preliminar": resultado_motor.es_preliminar,
            "variables": resultado_motor.variables,
            "razones": [r.model_dump() for r in resultado_motor.razones],
            "desglose": {k: v.model_dump() for k, v in resultado_motor.desglose_por_factor.items()},
        }

        try:
            perfil_version, operation_time = self._repositorio.guardar(
                cliente_id,
                fuentes,
                evaluacion=evaluacion,
                score_total=resultado_score["score_total"],
                nivel_riesgo=resultado_score["nivel_riesgo"],
                fecha_vigencia=resultado_score["fecha_vigencia"],
                version_reglas=resultado_score["version_reglas"],
                consentimiento_id=consentimiento_id,
                consentimiento_vence_en=consentimiento_vence_en,
            )
        except TypeError:
            perfil_version, operation_time = self._repositorio.guardar(cliente_id, fuentes)

        operation_time_str = serializar_operation_time(operation_time)

        if self._cache_score is not None:
            self._cache_score.guardar_score_cliente(cliente_id, perfil_version, resultado_score)

        logger.info(
            "perfil registrado cliente=%s perfil_version=%s score=%s nivel_riesgo=%s",
            id_opaco(cliente_id),
            perfil_version,
            resultado_score["score_total"],
            resultado_score["nivel_riesgo"],
        )

        evento = construir_evento_perfil_actualizado(
            cliente_id,
            perfil_version,
            operation_time_str,
            nivel_riesgo=resultado_score["nivel_riesgo"],
            fecha_vigencia=resultado_score["fecha_vigencia"],
            version_reglas=resultado_score["version_reglas"],
        )
        self._bus.publicar(evento)

        return {
            "cliente_id": cliente_id,
            "perfil_version": perfil_version,
            "operation_time": operation_time_str,
            "score": resultado_score["score_total"],
            "nivel_riesgo": resultado_score["nivel_riesgo"],
            "fecha_vigencia": resultado_score["fecha_vigencia"],
            "clasificacion": resultado_motor.clasificacion,
        }

    def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> int:
        """W01-A3: borra el perfil y su caché. Idempotente: sin perfil no hace nada. Las pólizas
        emitidas no se tocan, porque la propuesta ya copió el nivel de riesgo."""
        borrados = self._repositorio.borrar_de_consentimiento(titular_id, consentimiento_id)
        self._limpiar_cache(borrados)
        logger.info(
            "perfil borrado por revocación cliente=%s perfiles=%d",
            id_opaco(titular_id),
            len(borrados),
        )
        return len(borrados)

    def purgar_vencidos(self, ahora: datetime | None = None) -> int:
        """W01-A4: una pasada de la purga diaria. Idempotente."""
        borrados = self._repositorio.borrar_vencidos(ahora or datetime.now(UTC))
        self._limpiar_cache(borrados)
        logger.info("purga de perfiles vencidos perfiles=%d", len(borrados))
        return len(borrados)

    def _limpiar_cache(self, perfiles: list[dict[str, Any]]) -> None:
        if self._cache_score is None:
            return
        for perfil in perfiles:
            self._cache_score.borrar_perfil(
                perfil["cliente_id"], perfil["perfil_version"], perfil["version_reglas"]
            )

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
