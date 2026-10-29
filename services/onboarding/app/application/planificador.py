"""Quién dispara la verificación de una sesión ya enviada (mismo patrón que POLICY, W03-6).

En Cloud Run la CPU se limita fuera de una petición, así que con `DISPARADOR=externo` la
verificación la dispara **Cloud Tasks**: una tarea HTTP hacia `POST /tareas/verificar/{id}`
(token OIDC de la cuenta de servicio). En local y CI (`DISPARADOR=bucle`) corre en un hilo.

Encolar nunca tumba el envío: si falla se registra y la sesión sigue `EN_VERIFICACION`; volver a
enviarla la encola de nuevo. La verificación es idempotente (solo avanza desde EN_VERIFICACION)."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("onboarding.planificador")

URL_API_TAREAS = "https://cloudtasks.googleapis.com/v2"


class VerificacionPendiente(Exception):
    """La verificación no pudo avanzar por una falla transitoria (storage o ACL no respondieron).

    En la nube se responde 503 y Cloud Tasks reintenta con su backoff; en local lo reintenta el
    hilo. La sesión no cambia de estado."""


class PuertoPlanificador(Protocol):
    def encolar(self, sesion_id: str, demora_segundos: float = 0) -> None: ...


def _rfc3339(segundos: float) -> str:
    return datetime.fromtimestamp(segundos, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlanificadorLocal:
    """`DISPARADOR=bucle`: un hilo de este proceso. Solo para local y CI."""

    def __init__(self, demora_reintento_s: float = 2.0, max_reintentos: int = 5) -> None:
        self._verificar: Callable[[str], Any] | None = None
        self._demora_reintento_s = demora_reintento_s
        self._max_reintentos = max_reintentos

    def conectar(self, verificar: Callable[[str], Any]) -> None:
        """Se conecta después de construir el servicio (que necesita al planificador)."""
        self._verificar = verificar

    def encolar(self, sesion_id: str, demora_segundos: float = 0) -> None:
        hilo = threading.Timer(max(0.0, demora_segundos), self._correr, args=(sesion_id, 0))
        hilo.daemon = True
        hilo.start()

    def _correr(self, sesion_id: str, intento: int) -> None:
        if self._verificar is None:
            return
        try:
            self._verificar(sesion_id)
        except VerificacionPendiente:
            if intento + 1 < self._max_reintentos:
                hilo = threading.Timer(
                    self._demora_reintento_s, self._correr, args=(sesion_id, intento + 1)
                )
                hilo.daemon = True
                hilo.start()
            else:
                logger.warning("verificación sin avanzar tras %s intentos", self._max_reintentos)
        except Exception as error:  # un hilo de fondo nunca debe morir en silencio
            logger.warning("falló la verificación error=%s", type(error).__name__)


class PlanificadorCloudTasks:
    def __init__(
        self,
        proyecto: str,
        region: str,
        cola: str,
        url_destino: str,
        cuenta_servicio: str,
        sesion: Any | None = None,
        reloj: Any = time.time,
    ) -> None:
        self._cola = f"projects/{proyecto}/locations/{region}/queues/{cola}"
        self._base = url_destino.rstrip("/")
        self._cuenta = cuenta_servicio
        self._sesion = sesion
        self._reloj = reloj

    def _sesion_autenticada(self) -> Any:
        if self._sesion is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credenciales, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._sesion = AuthorizedSession(credenciales)
        return self._sesion

    def encolar(self, sesion_id: str, demora_segundos: float = 0) -> None:
        try:
            momento = self._reloj() + max(0, int(demora_segundos))
            # Agrupa por sesión y ventana de 10 s: enviar dos veces seguidas programa UNA tarea
            # (Cloud Tasks responde 409 al repetir el nombre). El id es un UUID opaco.
            nombre = f"{self._cola}/tasks/emision-{sesion_id}-{int(momento // 10)}"
            cuerpo = {
                "task": {
                    "name": nombre,
                    "scheduleTime": _rfc3339(momento),
                    "httpRequest": {
                        "httpMethod": "POST",
                        "url": f"{self._base}/tareas/verificar/{sesion_id}",
                        "oidcToken": {"serviceAccountEmail": self._cuenta, "audience": self._base},
                    },
                }
            }
            respuesta = self._sesion_autenticada().post(
                f"{URL_API_TAREAS}/{self._cola}/tasks", json=cuerpo, timeout=3
            )
            if respuesta.status_code not in (200, 409):
                logger.warning(
                    "no se pudo programar la verificación sesion=%s: HTTP %s",
                    id_opaco(sesion_id),
                    respuesta.status_code,
                )
        except Exception as error:  # encolar nunca debe tumbar el envío
            logger.warning(
                "no se pudo programar la verificación sesion=%s: %s",
                id_opaco(sesion_id),
                type(error).__name__,
            )
