"""Límite de tasa por credencial de socio (KAN-217/218, contrato v0.61).

Cada credencial (socio y entorno) tiene un tope de llamadas por segundo con token:
`LIMITE_SOCIO_RPS_PRODUCCION` (20 por defecto) y `LIMITE_SOCIO_RPS_PRUEBAS` (5). La que lo pasa
recibe **429 `LIMITE_DE_TASA`** con `Retry-After: 1` y `puedeReintentar = true`, antes de revisar
el scope, gastar presupuesto o llamar a otro servicio. Es distinto del presupuesto de cotizaciones
(`PRESUPUESTO_AGOTADO`, por hora y solo para cotizar): este protege al sistema de un socio que
llama demasiado rápido, y a los demás socios de él.

Ventana fija de 1 s en Redis (`INCR` + `EXPIRE`), compartida por todas las instancias del BFF. Si
Redis no responde, se deja pasar (regla 4) y se cuenta en `bff_socios_limite_sin_almacen_total`:
mejor un pico sin límite que cortar a todos los socios. Las rutas públicas (token, JWKS, contrato
y documentación) no pasan por aquí."""

import time
from typing import Any, Protocol

from prometheus_client import Counter

from app.socios.errores import ErrorApiSocio

CODIGO = "LIMITE_DE_TASA"
MENSAJE_LIMITE = "Demasiadas solicitudes por segundo para esta credencial. Intenta de nuevo."
RECHAZADAS = Counter(
    "bff_socios_limite_tasa_total",
    "Llamadas de socios rechazadas por el límite de tasa, por entorno.",
    ["entorno"],
)
SIN_ALMACEN = Counter(
    "bff_socios_limite_sin_almacen_total",
    "Llamadas de socios que pasaron sin límite porque Redis no respondió.",
)


class AlmacenVentanas(Protocol):
    async def contar(self, clave: str, ttl: int) -> int: ...


class AlmacenVentanasMemoria:
    """Para pruebas: sin vencimiento (cada segundo es una clave nueva)."""

    def __init__(self) -> None:
        self.contadores: dict[str, int] = {}

    async def contar(self, clave: str, ttl: int) -> int:
        self.contadores[clave] = self.contadores.get(clave, 0) + 1
        return self.contadores[clave]


class AlmacenVentanasRedis:
    def __init__(self, url: str) -> None:
        import redis.asyncio  # solo si hay Redis configurado

        self._redis: Any = redis.asyncio.Redis.from_url(
            url, socket_timeout=0.1, socket_connect_timeout=0.1
        )

    async def contar(self, clave: str, ttl: int) -> int:
        async with self._redis.pipeline(transaction=True) as tubo:
            tubo.incr(clave)
            tubo.expire(clave, ttl)
            valor, _ = await tubo.execute()
        return int(valor)

    async def aclose(self) -> None:
        await self._redis.aclose()


class LimiteTasa:
    def __init__(
        self,
        almacen: AlmacenVentanas,
        por_entorno: dict[str, int],
        reloj=time.time,
    ) -> None:
        self._almacen = almacen
        self._por_entorno = por_entorno
        self._reloj = reloj

    def tope(self, entorno: str) -> int:
        return self._por_entorno.get(entorno, min(self._por_entorno.values()))

    async def revisar(self, socio_id: str, entorno: str) -> None:
        tope = self.tope(entorno)
        if tope <= 0:  # 0 = sin límite (para pruebas de carga que lo quieran apagar)
            return
        segundo = int(self._reloj())
        try:
            llamadas = await self._almacen.contar(f"rl:socio:{socio_id}:{entorno}:{segundo}", 2)
        except Exception:  # noqa: BLE001
            SIN_ALMACEN.inc()
            return
        if llamadas > tope:
            RECHAZADAS.labels(entorno=entorno).inc()
            raise ErrorApiSocio(429, CODIGO, MENSAJE_LIMITE, True, None, 1)
