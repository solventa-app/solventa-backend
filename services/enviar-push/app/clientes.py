"""Cliente HTTP hacia AUTH (KAN-259): el único que sabe dónde mandar el push y el único que puede
marcar un token muerto. `enviar-push` nunca lee la base de AUTH directamente (escritor único)."""

import logging
from typing import Protocol

import httpx

logger = logging.getLogger("enviar_push.clientes")


class ErrorServicio(Exception):
    """AUTH no respondió a tiempo o respondió con un error que no es de negocio."""


class SinDispositivoActivo(Exception):
    """404 de AUTH: el cliente no tiene ningún dispositivo de push activo."""


class PushDeshabilitado(Exception):
    """409 de AUTH: el cliente apagó la preferencia de notificaciones."""


class PuertoAuth(Protocol):
    async def destino_push(self, usuario_id: str) -> dict: ...

    async def token_invalido(self, dispositivo_id: str) -> None: ...


class ClienteAuthHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def destino_push(self, usuario_id: str) -> dict:
        """`GET /interno/destino-push/{usuario_id}` -> {dispositivo_id, token, plataforma}."""
        try:
            respuesta = await self._cliente.get(f"/interno/destino-push/{usuario_id}")
        except httpx.HTTPError as error:
            raise ErrorServicio(type(error).__name__) from error
        if respuesta.status_code == 404:
            raise SinDispositivoActivo()
        if respuesta.status_code == 409:
            raise PushDeshabilitado()
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    async def token_invalido(self, dispositivo_id: str) -> None:
        """`POST /interno/dispositivos-push/{id}/token-invalido`. Idempotente: no falla si el
        dispositivo ya no existe o ya estaba sin token (AUTH no distingue, siempre 204)."""
        try:
            respuesta = await self._cliente.post(
                f"/interno/dispositivos-push/{dispositivo_id}/token-invalido"
            )
        except httpx.HTTPError as error:
            raise ErrorServicio(type(error).__name__) from error
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
