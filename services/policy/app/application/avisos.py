"""Avisos en vivo para el servicio `avisos` (SSE, HU-W03, W03-37).

Cuando una propuesta cambia de estado, POLICY publica en Redis un mensaje **sin datos personales**
(`propuestaId`, estado, estado visible y, desde EMITIDA, `polizaId` y número). `avisos` lo reenvía a
las pestañas abiertas:

- `avisos:asesor:<usuario>`: el ASESOR que aceptó la propuesta;
- `avisos:titular:<hmac>`: el titular (el CLIENTE con su cuenta verificada lo escucha);
- `avisos:todos`: el BACKOFFICE.

Es de mejor esfuerzo: si Redis no responde (p. ej. apagado de noche por «Costos staging»), se
registra y se sigue. La pantalla consulta al abrirse, así que no se pierde el estado real."""

import json
import logging
from typing import Any, Protocol

logger = logging.getLogger("policy.avisos")

ESTADOS_EMITIENDO = (
    "ACEPTADA",
    "IDENTIDAD_VERIFICADA",
    "EN_FIRMA",
    "FIRMADA",
    "EN_EMISION",
    "PENDIENTE_PAGO",
    "PAGO_RECHAZADO",
    "EN_REVISION",
)


def estado_visible(estado: str) -> str:
    """Los tres estados de la tabla «Mis pólizas» (W03-35)."""
    if estado == "EMITIDA":
        return "EMITIDA"
    if estado in ("NO_EMITIDA", "CANCELADA"):
        return "NO_EMITIDA"
    return "EMITIENDO"


class PuertoAvisos(Protocol):
    def publicar(
        self, propuesta: dict[str, Any], estado: str, poliza: dict | None = None
    ) -> None: ...


def mensaje(propuesta: dict[str, Any], estado: str, poliza: dict | None) -> str:
    return json.dumps(
        {
            "tipo": "propuesta",
            "propuestaId": propuesta["id"],
            "estado": estado,
            "estadoVisible": estado_visible(estado),
            "polizaId": poliza["id"] if poliza else None,
            "numero": poliza["numero_formateado"] if poliza else None,
        }
    )


def canales(propuesta: dict[str, Any]) -> list[str]:
    destinos = [f"avisos:titular:{propuesta['cliente_id']}", "avisos:todos"]
    if propuesta.get("actor_rol") == "ASESOR" and propuesta.get("actor_usuario_id"):
        destinos.insert(0, f"avisos:asesor:{propuesta['actor_usuario_id']}")
    return destinos


class AvisosRedis:
    def __init__(self, url: str) -> None:
        import redis  # solo si hay Redis configurado

        self._redis = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)

    def publicar(self, propuesta: dict[str, Any], estado: str, poliza: dict | None = None) -> None:
        cuerpo = mensaje(propuesta, estado, poliza)
        try:
            for canal in canales(propuesta):
                self._redis.publish(canal, cuerpo)
        except Exception as error:
            logger.warning("no se pudo publicar el aviso error=%s", type(error).__name__)


class AvisosNulos:
    """Sin `REDIS_URL` (pruebas, o un entorno sin Redis): no se publica nada."""

    def __init__(self) -> None:
        self.publicados: list[tuple[list[str], str]] = []

    def publicar(self, propuesta: dict[str, Any], estado: str, poliza: dict | None = None) -> None:
        self.publicados.append((canales(propuesta), mensaje(propuesta, estado, poliza)))
