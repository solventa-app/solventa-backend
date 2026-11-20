"""Antifraude mínimo de la API de socios (HA-SEG-004, KAN-66, contrato v0.60).

**Regla `SONDEO`** (enumerar lo que no es suyo o probar operaciones sin permiso): `UMBRAL_SONDEO`
respuestas 403 o 404 a la misma credencial (socio y entorno) en `VENTANA_SEGUNDOS`, contadas en una
ventana fija en Redis, la suspenden `SUSPENSION_SEGUNDOS`. Mientras dure, toda llamada con su token
responde 403 `CREDENCIAL_SUSPENDIDA` (con `Retry-After`) antes de revisar el scope o gastar
presupuesto.

La fuerza bruta sobre `POST /v1/token` no se repite aquí: AUTH ya bloquea la credencial tras 5
fallos (`services/auth/app/socios.py`) y responde siempre el mismo error a propósito, para no
revelar qué `clientId` existen ni cuáles están bloqueados.

La suspensión es por entorno: sondear con el token de PRUEBAS no corta PRODUCCIÓN. Vence sola; no
hay desbloqueo manual todavía. Cada suspensión deja un log de advertencia con el id del socio (no
es un dato personal) y suma `bff_socios_anomalia_total{regla}`. Si Redis no responde, la regla no
se aplica (regla 4): mejor dejar pasar que bloquear a todos los socios."""

import logging
import time
from typing import Any, Protocol

from prometheus_client import Counter

from app.socios.errores import ErrorApiSocio

logger = logging.getLogger("bff.socios.antifraude")

VENTANA_SEGUNDOS = 60
UMBRAL_SONDEO = 30
SUSPENSION_SEGUNDOS = 15 * 60
CODIGO = "CREDENCIAL_SUSPENDIDA"
MENSAJE_SUSPENDIDA = (
    "La credencial quedó suspendida temporalmente por actividad inusual. Intenta más tarde."
)

ANOMALIAS = Counter(
    "bff_socios_anomalia_total",
    "Credenciales de socio suspendidas por el antifraude, por regla.",
    ["regla"],
)
FALLAS_ALMACEN = Counter(
    "bff_socios_antifraude_sin_almacen_total",
    "Revisiones del antifraude que no se aplicaron porque Redis no respondió.",
)


class AlmacenContadores(Protocol):
    async def incrementar(self, clave: str, ttl: int) -> int: ...

    async def suspender(self, clave: str, ttl: int) -> None: ...

    async def restante(self, clave: str) -> int:
        """Segundos que le quedan a la suspensión; 0 si no hay."""
        ...


class AlmacenContadoresMemoria:
    """Para pruebas. `ahora` se puede mover para simular el paso del tiempo."""

    def __init__(self) -> None:
        self.ahora = time.monotonic
        self._contadores: dict[str, tuple[int, float]] = {}
        self._suspendidas: dict[str, float] = {}

    async def incrementar(self, clave: str, ttl: int) -> int:
        valor, vence = self._contadores.get(clave, (0, 0.0))
        if vence <= self.ahora():
            valor, vence = 0, self.ahora() + ttl
        self._contadores[clave] = (valor + 1, vence)
        return valor + 1

    async def suspender(self, clave: str, ttl: int) -> None:
        self._suspendidas[clave] = self.ahora() + ttl

    async def restante(self, clave: str) -> int:
        return max(0, int(self._suspendidas.get(clave, 0) - self.ahora()))


class AlmacenContadoresRedis:
    def __init__(self, url: str) -> None:
        import redis.asyncio  # solo si hay Redis configurado

        self._redis: Any = redis.asyncio.Redis.from_url(
            url, socket_timeout=0.2, socket_connect_timeout=0.2
        )

    async def incrementar(self, clave: str, ttl: int) -> int:
        async with self._redis.pipeline(transaction=True) as tubo:
            tubo.incr(clave)
            tubo.expire(clave, ttl, nx=True)
            valor, _ = await tubo.execute()
        return int(valor)

    async def suspender(self, clave: str, ttl: int) -> None:
        await self._redis.set(clave, "1", ex=ttl)

    async def restante(self, clave: str) -> int:
        return max(0, int(await self._redis.ttl(clave)))

    async def aclose(self) -> None:
        await self._redis.aclose()


def suspendida(restante: int) -> ErrorApiSocio:
    return ErrorApiSocio(403, CODIGO, MENSAJE_SUSPENDIDA, False, None, restante or None)


class Antifraude:
    def __init__(self, almacen: AlmacenContadores) -> None:
        self._almacen = almacen

    async def _restante(self, clave: str) -> int:
        try:
            return await self._almacen.restante(clave)
        except Exception:  # noqa: BLE001
            FALLAS_ALMACEN.inc()
            return 0

    async def _contar(self, contador: str, umbral: int, bloqueo: str, regla: str, quien: str):
        try:
            valor = await self._almacen.incrementar(contador, VENTANA_SEGUNDOS)
            # Solo al cruzar el umbral: la suspensión no se alarga con cada intento.
            if valor == umbral:
                await self._almacen.suspender(bloqueo, SUSPENSION_SEGUNDOS)
                ANOMALIAS.labels(regla=regla).inc()
                logger.warning("credencial de socio suspendida: regla=%s %s", regla, quien)
        except Exception:  # noqa: BLE001
            FALLAS_ALMACEN.inc()

    # ---------- SONDEO ----------

    async def revisar_credencial(self, socio_id: str, entorno: str) -> None:
        restante = await self._restante(f"af:bloqueo:socio:{socio_id}:{entorno}")
        if restante:
            raise suspendida(restante)

    async def respuesta_rechazada(self, socio_id: str, entorno: str) -> None:
        await self._contar(
            f"af:sondeo:{socio_id}:{entorno}",
            UMBRAL_SONDEO,
            f"af:bloqueo:socio:{socio_id}:{entorno}",
            "SONDEO",
            f"socio={socio_id} entorno={entorno}",
        )
