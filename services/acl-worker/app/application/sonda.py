"""Sonda de recuperación del circuito, FUERA del camino síncrono (regla de
arquitectura #5 — lección literal del Experimento 1): si el half-open de
`purgatory` se probara con la petición real de un usuario, ese usuario
pagaría otra vez el timeout del proveedor caído.

Cómo funciona (ver el docstring de `circuitos.py`): `purgatory` no tiene un
evento "el circuito lleva N segundos abierto" ni un método para forzar el
half-open — su única noción de "ya puedo reintentar" es perezosa y se
reevalúa en el PRÓXIMO `with breaker:` después de que venza el TTL. Esta
sonda corre un bucle `asyncio` en segundo plano (arrancado desde el lifespan
de FastAPI) que, cada `intervalo_s`, revisa qué breakers están abiertos y, si
encuentra uno, es ELLA quien entra a `with breaker:` primero — pagando el
costo de la llamada real al adaptador (en un hilo, vía `asyncio.to_thread`,
para no bloquear el loop de eventos) — antes de que llegue una petición de
usuario.

Esto no es 100% infalible: si una petición de usuario llega exactamente en la
ventana entre dos ticks de la sonda justo cuando el TTL ya venció, esa
petición podría ganarle a la sonda. Se mitiga con un `intervalo_s` sensiblemente
menor al TTL del breaker (por defecto 2 s de intervalo vs. 5 s de TTL), igual
de honesto que la limitación ya documentada de `purgatory` ("sus contadores no
tienen locks") en `docs/arquitectura-backend.md`.
"""

import asyncio
import logging
from collections.abc import Callable

import purgatory

from app.application.circuitos import CircuitoAbiertoError

logger = logging.getLogger("acl_worker.sonda")

Sondeo = Callable[[], None]


class SondaRecuperacion:
    def __init__(
        self,
        fabrica_circuitos: purgatory.SyncCircuitBreakerFactory,
        sondeos: dict[str, Sondeo],
        intervalo_s: float,
    ) -> None:
        self._fabrica = fabrica_circuitos
        self._sondeos = sondeos
        self._intervalo_s = intervalo_s
        self._tarea: asyncio.Task | None = None

    def iniciar(self) -> None:
        self._tarea = asyncio.create_task(self._bucle())

    async def detener(self) -> None:
        if self._tarea is None:
            return
        self._tarea.cancel()
        try:
            await self._tarea
        except asyncio.CancelledError:
            pass

    async def _bucle(self) -> None:
        while True:
            await asyncio.sleep(self._intervalo_s)
            for nombre, sondeo in self._sondeos.items():
                await asyncio.to_thread(self._intentar, nombre, sondeo)

    def _intentar(self, nombre: str, sondeo: Sondeo) -> None:
        brk = self._fabrica.get_breaker(nombre)
        if brk.context.state != "opened":
            return  # sano: nada que sondear, no se gasta ninguna llamada de más
        try:
            with self._fabrica.get_breaker(nombre):
                sondeo()
            logger.info("sonda circuito=%s: proveedor recuperado, circuito cerrado", nombre)
        except CircuitoAbiertoError:
            pass  # el TTL todavía no venció: fail-fast esperado, no se gastó red
        except Exception as error:
            logger.info(
                "sonda circuito=%s: proveedor aún degradado (%s)", nombre, type(error).__name__
            )
