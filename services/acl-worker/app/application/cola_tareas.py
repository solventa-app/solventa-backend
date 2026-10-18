"""Cola de reconciliación sobre Cloud Tasks (decisión W05-7, plan de HU-W05, bloque 0).

Con `DISPARADOR=externo` (Cloud Run) la reconciliación diferida de fuentes degradadas no usa `rq`:
su trabajador es un hilo de fondo que en Cloud Run no corre de forma fiable (CPU limitada fuera de
una petición, escala a cero). En su lugar, cada trabajo es una tarea HTTP de Cloud Tasks hacia
`POST /reconciliar` del consolidador, autenticada con un token OIDC de la cuenta de runtime del
ACL Worker.

El reintento con backoff lo hace la propia cola (`infra/tareas.tf`: 5 intentos, de 10 s a 300 s,
como el `rq.Retry` original): el consolidador responde 503 mientras la fuente siga degradada.
Agotados los intentos la tarea se descarta; no hay un registro de fallidos como el
`FailedJobRegistry` de `rq`.

Mismo contrato que `ColaReconciliacionRQ` (`PuertoColaReconciliacion`): quien encola no cambia."""

import base64
import json
from typing import Any

from app.application.reconciliacion import TrabajoReconciliacion

URL_API_TAREAS = "https://cloudtasks.googleapis.com/v2"
RUTA_RECONCILIAR = "/reconciliar"


class ColaReconciliacionCloudTasks:
    def __init__(
        self,
        proyecto: str,
        region: str,
        cola: str,
        url_consolidador: str,
        cuenta_servicio: str,
        sesion: Any | None = None,
    ) -> None:
        self._cola = f"projects/{proyecto}/locations/{region}/queues/{cola}"
        self._audiencia = url_consolidador.rstrip("/")
        self._url = self._audiencia + RUTA_RECONCILIAR
        self._cuenta = cuenta_servicio
        self._sesion = sesion

    def _sesion_autenticada(self) -> Any:
        if self._sesion is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credenciales, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._sesion = AuthorizedSession(credenciales)
        return self._sesion

    def encolar(self, trabajo: TrabajoReconciliacion) -> None:
        """Lanza si Cloud Tasks no acepta la tarea: `ProductorReconciliacion` lo registra y sigue
        (fire-and-forget), igual que con `rq`."""
        cuerpo = json.dumps(trabajo.a_dict()).encode("utf-8")
        tarea = {
            "task": {
                "httpRequest": {
                    "httpMethod": "POST",
                    "url": self._url,
                    "headers": {"Content-Type": "application/json"},
                    "body": base64.b64encode(cuerpo).decode("ascii"),
                    "oidcToken": {
                        "serviceAccountEmail": self._cuenta,
                        "audience": self._audiencia,
                    },
                }
            }
        }
        respuesta = self._sesion_autenticada().post(
            f"{URL_API_TAREAS}/{self._cola}/tasks", json=tarea, timeout=3
        )
        if respuesta.status_code != 200:
            raise RuntimeError(f"Cloud Tasks respondió HTTP {respuesta.status_code}")
