"""Retry acotado al presupuesto de latencia duro de 700 ms (T-W01-10, EC009/EC010).

Deliberadamente simple (sin backoff exponencial): el presupuesto total es tan
chico (700 ms) que esperar entre intentos le resta tiempo útil al segundo
intento sin aportar nada. Se reintenta UNA sola vez, y solo si el primer
intento falló dejando presupuesto de verdad (p. ej. una conexión rechazada de
inmediato) — si el primer intento ya agotó el presupuesto (p. ej. un timeout),
reintentar no cambiaría el resultado y sí violaría el corte duro de 700 ms.

Se usa `time.monotonic()`, no el reloj de pared: Docker Desktop da saltos de
reloj (~2.1 s cada ~30 s, ver `docs/arquitectura-backend.md`) que distorsionan
cualquier medición de presupuesto restante.
"""

import time
from collections.abc import Callable

MARGEN_MINIMO_S = 0.05


def con_reintento[T](
    accion: Callable[[float], T],
    presupuesto_s: float,
    margen_minimo_s: float = MARGEN_MINIMO_S,
) -> T:
    """Ejecuta `accion(presupuesto)`. Si falla y queda presupuesto > `margen_minimo_s`,
    reintenta UNA vez con el presupuesto restante; si no, relanza el error original."""
    inicio = time.monotonic()
    try:
        return accion(presupuesto_s)
    except Exception:
        restante = presupuesto_s - (time.monotonic() - inicio)
        if restante <= margen_minimo_s:
            raise
        return accion(restante)
