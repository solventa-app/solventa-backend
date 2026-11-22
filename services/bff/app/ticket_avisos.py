"""Ticket de un solo uso para abrir el stream SSE del servicio `avisos` (HU-W03, W03-37).

`EventSource` no manda cabeceras, así que el front pide un ticket al BFF y abre
`GET <avisos>/avisos/stream?ticket=…`. El JWT nunca va en la URL. El ticket vive 60 s en Redis y
`avisos` lo consume una sola vez; guarda quién es el que escucha (rol, usuario y, para un CLIENTE,
su titular) para que `avisos` elija los canales. No lleva datos personales."""

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger("bff.avisos")

VIGENCIA_TICKET_SEGUNDOS = 60
PREFIJO_TICKET = "avisos:ticket:"


class PuertoTickets(Protocol):
    async def emitir(
        self, rol: str, usuario_id: str, titular_id: str | None, entorno: str | None = None
    ) -> tuple[str, datetime]:
        """El ticket y cuándo vence. Lanza cualquier excepción si Redis no responde. `entorno` solo
        lo lleva el de un SOCIO: escucha el canal de ese entorno (contrato v0.67)."""
        ...

    async def publicar(self, canal: str, mensaje: str) -> None:
        """Publica un aviso en un canal de Redis. Lanza cualquier excepción si Redis no responde."""
        ...


def canal_socio(socio_id: str, entorno: str) -> str:
    """El canal del panel del socio en un entorno (el mismo que arma `avisos/app/canales.py`)."""
    return f"avisos:socio:{socio_id}:{entorno}"


class TicketsRedis:
    def __init__(self, url: str) -> None:
        import redis.asyncio  # solo si hay Redis configurado

        self._redis: Any = redis.asyncio.Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5
        )

    async def emitir(
        self, rol: str, usuario_id: str, titular_id: str | None, entorno: str | None = None
    ) -> tuple[str, datetime]:
        ticket = secrets.token_urlsafe(32)
        datos = {"rol": rol, "usuarioId": usuario_id, "titularId": titular_id}
        if entorno:
            datos["entorno"] = entorno
        cuerpo = json.dumps(datos)
        await self._redis.set(PREFIJO_TICKET + ticket, cuerpo, ex=VIGENCIA_TICKET_SEGUNDOS)
        return ticket, datetime.now(UTC) + timedelta(seconds=VIGENCIA_TICKET_SEGUNDOS)

    async def publicar(self, canal: str, mensaje: str) -> None:
        await self._redis.publish(canal, mensaje)

    async def aclose(self) -> None:
        await self._redis.aclose()
