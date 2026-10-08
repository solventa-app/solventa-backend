"""Lectura del perfil de riesgo sobre la réplica de lectura (RATING **nunca**
escribe en Riesgo — regla de arquitectura #1). Implementa el mecanismo de
D-02/HA-LAT-003 (read-your-writes vía sesión causal):

1. Sesión con `causal_consistency=True` y `session.advance_operation_time(...)`
   fijado al `operationTime` que RISK devolvió en la escritura — eso agrega
   `afterClusterTime` a los comandos de esa sesión.
2. Lectura con `read_preference=SECONDARY`, acotada con `maxTimeMS` (umbral
   corto, coherente con EC011/EC012: p95 <=150ms, p99 <=300ms de presupuesto
   total). El servidor espera a que la secundaria alcance ese punto de corte
   antes de responder; si no lo alcanza dentro del plazo, la operación falla
   con `MaxTimeMSExpired` (pymongo la traduce a `ExecutionTimeout`, a veces
   `OperationFailure`).
3. D-02, alternativa explícita del plan: si eso ocurre, se cae a leer de la
   PRIMARIA dentro de la MISMA solicitud — nunca un error al usuario por este
   motivo.

**Hueco cerrado (hallazgo de la verificación en vivo anterior, ver README):**
con la secundaria totalmente INALCANZABLE (no solo atrasada), la selección de
servidor de pymongo corre ANTES de que exista una operación a la que aplicar
`maxTimeMS`, y por defecto tarda hasta 30s (`serverSelectionTimeoutMS`) — ese
caso no lo cubría el `except` original. Por eso la lectura de la secundaria
usa un `MongoClient` DEDICADO con `serverSelectionTimeoutMS` acotado al mismo
presupuesto (`max_time_ms`): si no encuentra una secundaria utilizable a
tiempo, falla rápido con `ServerSelectionTimeoutError` (agregado a
`ERRORES_FALLBACK_A_PRIMARIA`) y cae a la primaria igual que con
`MaxTimeMSExpired`. El cliente de la PRIMARIA conserva el timeout de
selección de servidor por defecto (más generoso): un fallback no debe
fallar rápido por el mismo motivo que la secundaria.

`leer_con_fallback_a_primaria` aísla esa decisión (intentar secundaria, caer
a primaria si se agota el tiempo) como una función pura e inyectable, para
poder probarla con dobles sin necesitar un MongoDB real (ver
`tests/test_lector_perfiles.py`); `LectorPerfilesMongo` la usa con las
llamadas reales a pymongo — esa parte sí requiere el replica set real y se
verifica en vivo con `docker compose` (ver README.md).

Cache-Aside de scores en Redis (HA-LAT-001) y el cálculo de la oferta/prima
quedan fuera de alcance de esta tarea (T-W01-6 completo, Sprint 2 por el
README de este servicio): este módulo solo lee el documento de perfil tal
cual RISK lo escribió."""

import logging
from collections.abc import Callable
from typing import Any, Protocol

from pymongo import MongoClient
from pymongo.errors import (
    AutoReconnect,
    ExecutionTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)
from pymongo.read_preferences import Secondary

from app.application.identificadores import id_opaco
from app.application.tiempo_causal import deserializar_operation_time

logger = logging.getLogger("rating.lector")

NOMBRE_BASE = "risk"
COLECCION_PERFILES = "perfiles"

# Errores que señalan que la secundaria no alcanzó el afterClusterTime dentro
# de maxTimeMS, o que ni siquiera pudo encontrarse/alcanzarse una secundaria
# utilizable a tiempo (secundaria inalcanzable, no solo atrasada — ver nota
# arriba): `ServerSelectionTimeoutError` si la topología ya sabía que no había
# ninguna buena; `AutoReconnect` (incluye `NetworkTimeout`) si la topología
# creía que sí había una, pero el socket se quedó colgado al usarla
# (`docker pause`, caída de red a medio camino, etc. — confirmado en vivo).
# Ante cualquiera de estos, se cae a la primaria en la misma solicitud.
ERRORES_FALLBACK_A_PRIMARIA: tuple[type[Exception], ...] = (
    ExecutionTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
    AutoReconnect,
)


def leer_con_fallback_a_primaria[T](
    leer_de_secundaria: Callable[[], T],
    leer_de_primaria: Callable[[], T],
    errores_fallback: tuple[type[Exception], ...] = ERRORES_FALLBACK_A_PRIMARIA,
) -> tuple[T, bool]:
    """Devuelve `(resultado, de_secundaria)`. Intenta `leer_de_secundaria`
    primero; si lanza uno de `errores_fallback`, cae a `leer_de_primaria` en
    la MISMA llamada (nunca un error al llamador por ese motivo). Cualquier
    otra excepción se propaga tal cual: el fallback es específico de
    "la secundaria no llegó a tiempo", no un manejo general de errores."""
    try:
        return leer_de_secundaria(), True
    except errores_fallback:
        return leer_de_primaria(), False


class LectorPerfiles(Protocol):
    def leer(self, cliente_id: str, operation_time: str) -> dict[str, Any] | None: ...


class LectorPerfilesMongo:
    """Dos clientes deliberadamente distintos (ver nota de arriba): uno para
    la secundaria, con `serverSelectionTimeoutMS` acotado al mismo
    presupuesto que `max_time_ms` (para que una secundaria INALCANZABLE
    también falle rápido y dispare el fallback), y uno para la primaria, con
    el timeout de selección por defecto (un fallback no debe ser tan frágil
    como el camino que intenta evitar)."""

    def __init__(
        self,
        cliente_primaria: MongoClient,
        cliente_secundaria: MongoClient,
        max_time_ms: int,
    ) -> None:
        self._coleccion_primaria = cliente_primaria[NOMBRE_BASE][COLECCION_PERFILES]
        self._coleccion_secundaria = cliente_secundaria[NOMBRE_BASE][
            COLECCION_PERFILES
        ].with_options(read_preference=Secondary())
        self._cliente_secundaria = cliente_secundaria
        self._max_time_ms = max_time_ms

    def leer(self, cliente_id: str, operation_time: str) -> dict[str, Any] | None:
        punto_corte = deserializar_operation_time(operation_time)

        def _de_secundaria() -> dict[str, Any] | None:
            with self._cliente_secundaria.start_session(causal_consistency=True) as session:
                session.advance_operation_time(punto_corte)
                return self._coleccion_secundaria.find_one(
                    {"cliente_id": cliente_id},
                    sort=[("perfil_version", -1)],
                    session=session,
                    max_time_ms=self._max_time_ms,
                )

        def _de_primaria() -> dict[str, Any] | None:
            # Sin sesión causal: la primaria siempre tiene el dato más
            # reciente, no hay `afterClusterTime` que esperar.
            return self._coleccion_primaria.find_one(
                {"cliente_id": cliente_id},
                sort=[("perfil_version", -1)],
            )

        documento, de_secundaria = leer_con_fallback_a_primaria(_de_secundaria, _de_primaria)

        if not de_secundaria:
            logger.warning(
                "secundaria no alcanzo operationTime a tiempo (maxTimeMS=%s) cliente=%s; "
                "se cayo a la primaria",
                self._max_time_ms,
                id_opaco(cliente_id),
            )

        if documento is None:
            return None

        return {
            "cliente_id": documento["cliente_id"],
            "perfil_version": documento["perfil_version"],
            "fuentes": documento["fuentes"],
            "leido_de_secundaria": de_secundaria,
        }
