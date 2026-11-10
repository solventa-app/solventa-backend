"""Sesiones cerradas cuyo token de acceso aún no vence (autenticación, fase 3).

El token dura minutos y lleva `sid`, la cadena de refresh a la que pertenece. Al cerrar la sesión
AUTH revoca el refresh; aquí se anota el `sid` hasta que el token venza, para que ese acceso
tampoco sirva si alguien lo conservó. Si Redis no responde la petición sigue (el token vence
solo en minutos): no se tumba el servicio por esto."""

import logging
from typing import Any, Protocol

logger = logging.getLogger("bff.sesiones_revocadas")

PREFIJO = "sesion-cerrada:"


class PuertoSesionesRevocadas(Protocol):
    async def revocar(self, sid: str, segundos: int) -> None: ...

    async def esta_revocada(self, sid: str) -> bool: ...


class SesionesRevocadasMemoria:
    def __init__(self) -> None:
        self.sids: set[str] = set()

    async def revocar(self, sid: str, segundos: int) -> None:
        self.sids.add(sid)

    async def esta_revocada(self, sid: str) -> bool:
        return sid in self.sids


class SesionesRevocadasRedis:
    def __init__(self, url: str) -> None:
        import redis.asyncio  # solo si hay Redis configurado

        self._redis: Any = redis.asyncio.Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5
        )

    async def revocar(self, sid: str, segundos: int) -> None:
        try:
            await self._redis.set(PREFIJO + sid, "1", ex=max(segundos, 1))
        except Exception:
            logger.warning("no se pudo anotar una sesión cerrada")

    async def esta_revocada(self, sid: str) -> bool:
        try:
            return bool(await self._redis.exists(PREFIJO + sid))
        except Exception:
            logger.warning("no se pudo consultar si la sesión estaba cerrada")
            return False

    async def aclose(self) -> None:
        await self._redis.aclose()
