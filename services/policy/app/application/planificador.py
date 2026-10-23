"""Quién dispara la emisión de una propuesta ya firmada (decisión W03-6).

En Cloud Run los servicios tienen la CPU limitada fuera de una petición y escalan a cero, así que un
hilo en segundo plano no corre de forma confiable (mismo motivo que PAYMENTS, decisión W05-7). Con
`DISPARADOR=externo` la emisión la dispara **Cloud Tasks**: una tarea HTTP hacia el propio
`POST /propuestas/{id}/_emitir` (autenticada con un token OIDC de la cuenta de servicio de POLICY).
En local y en CI (`DISPARADOR=bucle`) corre en un hilo de este proceso.

Encolar NUNCA tumba una firma ya registrada: si falla se registra y la propuesta sigue `EN_EMISION`;
volver a firmar (idempotente) la vuelve a encolar. La emisión misma es idempotente, así que una
tarea repetida no duplica nada."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.identificadores import id_opaco

logger = logging.getLogger("policy.planificador")

URL_API_TAREAS = "https://cloudtasks.googleapis.com/v2"


class EmisionPendiente(Exception):
    """La emisión no pudo avanzar por una falla transitoria (UNDER o PAYMENTS no respondieron).

    En la nube se responde 503 y Cloud Tasks reintenta con su backoff; en local lo reintenta el
    hilo. La propuesta no cambia de estado."""


class PuertoPlanificador(Protocol):
    def encolar(self, propuesta_id: str, demora_segundos: float = 0) -> None: ...


def _rfc3339(segundos: float) -> str:
    return datetime.fromtimestamp(segundos, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlanificadorLocal:
    """`DISPARADOR=bucle`: un hilo de este proceso. Solo para local y CI."""

    def __init__(self, demora_reintento_s: float = 2.0, max_reintentos: int = 5) -> None:
        self._emitir: Callable[[str], Any] | None = None
        self._demora_reintento_s = demora_reintento_s
        self._max_reintentos = max_reintentos

    def conectar(self, emitir: Callable[[str], Any]) -> None:
        """Se conecta después de construir el servicio de emisión (que necesita al planificador)."""
        self._emitir = emitir

    def encolar(self, propuesta_id: str, demora_segundos: float = 0) -> None:
        hilo = threading.Timer(max(0.0, demora_segundos), self._correr, args=(propuesta_id, 0))
        hilo.daemon = True
        hilo.start()

    def _correr(self, propuesta_id: str, intento: int) -> None:
        if self._emitir is None:
            return
        try:
            self._emitir(propuesta_id)
        except EmisionPendiente:
            if intento + 1 < self._max_reintentos:
                hilo = threading.Timer(
                    self._demora_reintento_s, self._correr, args=(propuesta_id, intento + 1)
                )
                hilo.daemon = True
                hilo.start()
            else:
                logger.warning("emisión sin avanzar tras %s intentos", self._max_reintentos)
        except Exception as error:  # un hilo de fondo nunca debe morir en silencio
            logger.warning("falló la emisión error=%s", type(error).__name__)


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

    def encolar(self, propuesta_id: str, demora_segundos: float = 0) -> None:
        try:
            momento = self._reloj() + max(0, int(demora_segundos))
            # El nombre de la tarea agrupa por propuesta y por ventana de 10 s: encolar varias
            # veces seguidas (p. ej. firmar dos veces) programa UNA sola tarea (Cloud Tasks
            # responde 409 al repetir el nombre). El id de la propuesta es un UUID, no un dato
            # personal.
            nombre = f"{self._cola}/tasks/emision-{propuesta_id}-{int(momento // 10)}"
            cuerpo = {
                "task": {
                    "name": nombre,
                    "scheduleTime": _rfc3339(momento),
                    "httpRequest": {
                        "httpMethod": "POST",
                        "url": f"{self._base}/propuestas/{propuesta_id}/_emitir",
                        "oidcToken": {"serviceAccountEmail": self._cuenta, "audience": self._base},
                    },
                }
            }
            respuesta = self._sesion_autenticada().post(
                f"{URL_API_TAREAS}/{self._cola}/tasks", json=cuerpo, timeout=3
            )
            if respuesta.status_code not in (200, 409):
                logger.warning(
                    "no se pudo programar la emisión propuesta=%s: HTTP %s",
                    id_opaco(propuesta_id),
                    respuesta.status_code,
                )
        except Exception as error:  # encolar nunca debe tumbar una firma
            logger.warning(
                "no se pudo programar la emisión propuesta=%s: %s",
                id_opaco(propuesta_id),
                type(error).__name__,
            )
