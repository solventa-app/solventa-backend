"""Eventos `onboarding.verificado` y `onboarding.rechazado` (HU-M01).

Punto de extensión del aviso del resultado: un consumidor futuro (la push) se suscribe sin tocar
este servicio. Mismo patrón que `services/almacenamiento/app/application/bus_eventos.py`: Pub/Sub
por su API REST si hay tema configurado; si no, el evento se loguea. Publicar nunca tumba la
verificación (regla 4). Solo llevan identificadores opacos y códigos (regla 6). El esquema entra a
`contracts/events/` con la v0.20 del contrato."""

import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("onboarding.eventos")

URL_API_PUBSUB = "https://pubsub.googleapis.com/v1"


def construir_evento(tipo_evento: str, sesion: dict[str, Any]) -> dict[str, Any]:
    datos: dict[str, Any] = {"onboardingId": sesion["id"], "intento": sesion["intento"]}
    if sesion.get("usuario_id"):
        datos["usuarioId"] = sesion["usuario_id"]
    if tipo_evento == "onboarding.rechazado":
        datos["motivo"] = sesion["motivo"]
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
        logger.info(
            "evento=%s eventId=%s sesion=%s [LOG: sin tema de Pub/Sub]",
            evento["tipo"],
            evento["eventId"],
            id_opaco(evento["datos"]["onboardingId"]),
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
            "attributes": {"tipoEvento": evento["tipo"], "version": str(evento["version"])},
        }
        try:
            respuesta = self._sesion_autenticada().post(
                f"{URL_API_PUBSUB}/{tema}:publish", json={"messages": [mensaje]}, timeout=5
            )
            if respuesta.status_code != 200:
                raise RuntimeError(f"HTTP {respuesta.status_code}")
        except Exception as error:  # publicar nunca tumba la verificación
            logger.warning(
                "no se pudo publicar evento=%s sesion=%s error=%s",
                evento["tipo"],
                id_opaco(evento["datos"]["onboardingId"]),
                type(error).__name__,
            )


def construir_bus(tema_verificado: str, tema_rechazado: str) -> PuertoBusEventos:
    if tema_verificado or tema_rechazado:
        return BusEventosPubSub(
            {"onboarding.verificado": tema_verificado, "onboarding.rechazado": tema_rechazado}
        )
    return BusEventosLog()
