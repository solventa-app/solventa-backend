"""Orquesta un `notificacion.push-solicitada` ya validado: plantilla -> destino en AUTH -> SNS.

Nunca deja un fallo de negocio (sin dispositivo, push apagado, plantilla desconocida) como algo
para reintentar: esos se confirman (regla #4, nunca bloquear al productor; CA-M04-07). Solo un
fallo transitorio (AUTH caído, SNS caído) se propaga para que Pub/Sub reintente con su backoff."""

import logging
from typing import Any

from app.clientes import PuertoAuth, PushDeshabilitado, SinDispositivoActivo
from app.plantillas import PlantillaDesconocida, Plantillas
from app.sns import PuertoEnviadorPush, TokenInvalido

logger = logging.getLogger("enviar_push.servicio")


class ServicioEnviarPush:
    def __init__(
        self, auth: PuertoAuth, enviador: PuertoEnviadorPush, plantillas: Plantillas
    ) -> None:
        self._auth = auth
        self._enviador = enviador
        self._plantillas = plantillas

    async def procesar(self, evento: dict[str, Any]) -> str:
        """Devuelve el resultado como texto (para el log del llamador): `ENVIADO`, `SIN_DESTINO`,
        `PUSH_DESHABILITADO`, `PLANTILLA_DESCONOCIDA` o `TOKEN_INVALIDO`. Cualquiera de estas
        termina el procesamiento del evento (no se reintenta)."""
        datos = evento["datos"]
        try:
            mensaje_plantilla = self._plantillas.resolver(
                datos["plantilla"], datos.get("variables")
            )
        except PlantillaDesconocida:
            logger.error("plantilla desconocida=%s; evento descartado", datos["plantilla"])
            return "PLANTILLA_DESCONOCIDA"

        try:
            destino = await self._auth.destino_push(datos["usuarioId"])
        except SinDispositivoActivo:
            logger.info("sin dispositivo activo; push confirmado sin enviar")
            return "SIN_DESTINO"
        except PushDeshabilitado:
            logger.info("push deshabilitado por el cliente; push confirmado sin enviar")
            return "PUSH_DESHABILITADO"

        mensaje = {
            **mensaje_plantilla,
            "idempotencia": datos["idempotencia"],
            "ruta": datos.get("ruta"),
            "evento_id": evento["eventId"],
        }
        if "ttlSegundos" in datos:
            mensaje["ttl_segundos"] = datos["ttlSegundos"]
        if "silenciosa" in datos:
            mensaje["silenciosa"] = datos["silenciosa"]

        try:
            self._enviador.enviar(destino, mensaje)
        except TokenInvalido:
            await self._auth.token_invalido(destino["dispositivo_id"])
            logger.info("token invalido; dispositivo marcado en AUTH, push confirmado")
            return "TOKEN_INVALIDO"

        logger.info("push enviado idempotencia=%s", datos["idempotencia"])
        return "ENVIADO"
