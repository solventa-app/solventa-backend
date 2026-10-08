"""Wiring del Circuit Breaker (`purgatory`) del ACL Worker (HA-LAT-002).

Un breaker por ADAPTADOR/proveedor (`open-finance`, `open-data`, `pasarela`),
no por fuente individual: si Open Finance cae, afecta a sus 2 fuentes por
igual (tal como pide la tarea T-W01-10).

`purgatory` no expone un modo "forzar half-open" ni un evento "circuito
abierto hace N segundos": su modelo es perezoso (ver `OpenedState` más abajo)
y el estado solo se reevalúa cuando algo vuelve a entrar al `with breaker:`.
Esa misma pereza es la que `app/application/sonda.py` explota a propósito para
que la sonda de recuperación (regla de arquitectura #5), no una petición de
usuario, sea quien dispare esa reevaluación.
"""

import purgatory
from purgatory.domain.model import OpenedState

# Alias con nombre de dominio: se lanza cuando el circuito está abierto y el TTL
# de recuperación todavía no venció (fail-fast; nunca se llega a intentar la
# llamada real al proveedor).
CircuitoAbiertoError = OpenedState

NOMBRE_CIRCUITO_OPEN_FINANCE = "open-finance"
NOMBRE_CIRCUITO_OPEN_DATA = "open-data"
NOMBRE_CIRCUITO_PASARELA = "pasarela"


def construir_fabrica(
    umbral_fallos: int, ttl_segundos: float
) -> purgatory.SyncCircuitBreakerFactory:
    """Una sola fábrica de breakers por proceso; el estado vive en memoria
    (`SyncInMemoryUnitOfWork`, el default de `purgatory`). Limitación conocida
    y aceptada (igual que en el Experimento 1): sin locks entre hilos y sin
    persistencia entre reinicios del proceso — ver README de este servicio.
    """
    return purgatory.SyncCircuitBreakerFactory(
        default_threshold=umbral_fallos,
        default_ttl=ttl_segundos,
    )
