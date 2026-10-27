"""Eventos `archivo.disponible` y `archivo.rechazado` (KAN-250).

Cada HU que use el storage se entera de que su archivo quedó listo suscribiéndose a estos temas
con un filtro por el atributo `tipoArchivo` (p. ej. `attributes.tipoArchivo = "onboarding-video"`):
el storage no conoce a sus consumidores.

En la nube se publica en Pub/Sub por su API REST (sin el cliente gRPC). Es una ampliación de la
decisión W05-8 («Pub/Sub real solo para `perfil.actualizado`»), registrada en ADR-10: el storage
ya depende de Pub/Sub para recibir las notificaciones del bucket. En local y CI (sin temas
configurados) el evento se loguea. Publicar nunca tumba la confirmación: si Pub/Sub falla se
loguea y el archivo queda en su estado igual (regla 4).

Los eventos cumplen `contracts/events/archivo.*.schema.json` (validado en las pruebas) y solo
llevan identificadores opacos (regla 6)."""

import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("almacenamiento.eventos")

URL_API_PUBSUB = "https://pubsub.googleapis.com/v1"


def construir_evento(tipo_evento: str, archivo: dict[str, Any]) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "archivoId": archivo["id"],
        "tipoArchivo": archivo["tipo"],
        "propietarioTipo": archivo["propietario_tipo"],
        "propietarioId": archivo["propietario_id"],
    }
    if tipo_evento == "archivo.disponible":
        datos["tipoContenido"] = archivo["tipo_contenido"]
        datos["tamanoBytes"] = int(archivo["tamano_bytes"])
        if archivo.get("derivado_de"):
            datos["derivadoDe"] = archivo["derivado_de"]
    else:
        datos["motivo"] = archivo["motivo"]
    return {
        "eventId": str(uuid.uuid4()),
        "tipo": tipo_evento,
        "version": 1,
        "ocurridoEn": datetime.now(UTC).isoformat(),
        "datos": datos,
    }


class PuertoBusEventos(Protocol):
    def publicar(self, evento: dict[str, Any]) -> None: ...


class BusEventosLog:
    def publicar(self, evento: dict[str, Any]) -> None:
        datos = evento["datos"]
        logger.info(
            "evento=%s eventId=%s archivo=%s tipoArchivo=%s [LOG: sin tema de Pub/Sub]",
            evento["tipo"],
            evento["eventId"],
            id_opaco(datos["archivoId"]),
            datos["tipoArchivo"],
        )


class BusEventosPubSub:
    def __init__(self, temas: dict[str, str], sesion: Any | None = None) -> None:
        self._temas = temas
        self._sesion = sesion
        self._respaldo = BusEventosLog()

    def _sesion_autenticada(self) -> Any:
        if self._sesion is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credenciales, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/pubsub"])
            self._sesion = AuthorizedSession(credenciales)
        return self._sesion

    def publicar(self, evento: dict[str, Any]) -> None:
        tema = self._temas.get(evento["tipo"])
        if not tema:
            self._respaldo.publicar(evento)
            return
        mensaje = {
            "data": base64.b64encode(json.dumps(evento).encode("utf-8")).decode("ascii"),
            "attributes": {
                "tipoEvento": evento["tipo"],
                "version": str(evento["version"]),
                "tipoArchivo": evento["datos"]["tipoArchivo"],
            },
        }
        try:
            respuesta = self._sesion_autenticada().post(
                f"{URL_API_PUBSUB}/{tema}:publish", json={"messages": [mensaje]}, timeout=5
            )
            if respuesta.status_code != 200:
                raise RuntimeError(f"HTTP {respuesta.status_code}")
            logger.info(
                "evento=%s eventId=%s archivo=%s [PUBSUB]",
                evento["tipo"],
                evento["eventId"],
                id_opaco(evento["datos"]["archivoId"]),
            )
        except Exception as error:  # publicar nunca tumba la confirmación
            logger.warning(
                "no se pudo publicar evento=%s archivo=%s error=%s",
                evento["tipo"],
                id_opaco(evento["datos"]["archivoId"]),
                type(error).__name__,
            )


def construir_bus(tema_disponible: str, tema_rechazado: str) -> PuertoBusEventos:
    if tema_disponible or tema_rechazado:
        return BusEventosPubSub(
            {"archivo.disponible": tema_disponible, "archivo.rechazado": tema_rechazado}
        )
    return BusEventosLog()
