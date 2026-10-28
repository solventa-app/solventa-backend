"""Formato SSE y bucle de reenvío. El mensaje de Redis se reenvía tal cual: POLICY ya lo publica sin
datos personales. Ni el ticket ni el contenido de los mensajes se escriben en los logs."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.canales import CANAL_COBROS

logger = logging.getLogger("avisos.stream")

REINTENTO_MS = 3000
LATIDO = ": latido\n\n"


def evento_sse(tipo: str, datos: str) -> str:
    return f"event: {tipo}\ndata: {datos}\n\n"


def _como_texto(dato: Any) -> str:
    return dato.decode("utf-8") if isinstance(dato, bytes) else str(dato)


def _tipo_de(canal: str, datos: str) -> str:
    """`tipo` del mensaje (`propuesta`, `cobro`); si falta, el del canal."""
    try:
        tipo = json.loads(datos).get("tipo")
    except (ValueError, AttributeError):
        tipo = None
    if isinstance(tipo, str) and tipo.isidentifier():
        return tipo
    return "cobro" if canal == CANAL_COBROS else "propuesta"


async def reenviar(pubsub: Any, latido_segundos: float) -> AsyncIterator[str]:
    """Reenvía cada mensaje del suscriptor como evento SSE y manda un latido cuando pasa
    `latido_segundos` sin mensajes (mantiene viva la conexión tras los proxies). Termina si Redis
    falla: el front pide un ticket nuevo y vuelve a conectar (el ticket es de un solo uso)."""
    yield f"retry: {REINTENTO_MS}\n: conectado\n\n"
    while True:
        try:
            mensaje = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=latido_segundos
            )
        except Exception as error:
            logger.warning("se cortó la suscripción error=%s", type(error).__name__)
            return
        if mensaje is None:
            yield LATIDO
            continue
        datos = _como_texto(mensaje["data"])
        yield evento_sse(_tipo_de(_como_texto(mensaje["channel"]), datos), datos)
