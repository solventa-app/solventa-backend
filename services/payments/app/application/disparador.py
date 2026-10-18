"""Quién dispara el reintento de cobros pendientes (T-W05-4, CA-W05-05, decisión W05-7).

En Cloud Run los servicios tienen la CPU limitada fuera de una petición y escalan a cero, así que
un bucle `asyncio` en segundo plano no corre de forma confiable. Con `DISPARADOR=externo` el
reintento lo dispara **Cloud Tasks**: cuando un cobro queda `pendiente`, PAYMENTS programa una
tarea HTTP hacia su propio `POST /cobros/_reintentar` (autenticada con un token OIDC de su cuenta
de servicio), y esa pasada vuelve a programar la siguiente mientras queden pendientes. La lógica
de reintento es la misma de siempre (`reintento.ejecutar_una_pasada`); solo cambia quién la
dispara. En local y en CI sigue el bucle.

Programar una tarea NUNCA tumba un cobro: si Cloud Tasks falla, se registra y el cobro sigue
`pendiente` (el botón del back-office y la próxima pasada lo recuperan)."""

import logging
import time
from datetime import UTC, datetime
from typing import Any, Protocol

logger = logging.getLogger("payments.disparador")

URL_API_TAREAS = "https://cloudtasks.googleapis.com/v2"
RUTA_REINTENTO = "/cobros/_reintentar"
PREFIJO_TAREA = "reintento-cobros"


class PuertoPlanificador(Protocol):
    def programar(self, demora_segundos: float) -> None: ...


def _rfc3339(segundos: float) -> str:
    return datetime.fromtimestamp(segundos, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlanificadorNulo:
    """Con el bucle local no hay nada que programar."""

    def programar(self, demora_segundos: float) -> None:
        return None


class BusConReintento:
    """Envuelve al bus de eventos: cuando un cobro pasa a `pendiente`, programa el reintento.

    Así `ServicioCobros` y `procesar_reintento` no cambian: ya publican el evento en cada cambio."""

    def __init__(self, bus: Any, planificador: PuertoPlanificador, demora_segundos: float) -> None:
        self._bus = bus
        self._planificador = planificador
        self._demora_segundos = demora_segundos

    def publicar(self, evento: dict[str, Any]) -> None:
        self._bus.publicar(evento)
        if evento["datos"]["estado"] == "pendiente":
            self._planificador.programar(self._demora_segundos)


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
        self._url = url_destino.rstrip("/") + RUTA_REINTENTO
        self._audiencia = url_destino.rstrip("/")
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

    def programar(self, demora_segundos: float) -> None:
        try:
            ahora = self._reloj()
            demora = max(1, int(demora_segundos))
            momento = ahora + demora
            # El nombre agrupa las tareas por ventana: varias órdenes `pendiente` casi a la vez
            # programan UNA sola pasada (Cloud Tasks responde 409 al repetir el nombre).
            nombre = f"{self._cola}/tasks/{PREFIJO_TAREA}-{int(momento // max(demora, 10))}"
            cuerpo = {
                "task": {
                    "name": nombre,
                    "scheduleTime": _rfc3339(momento),
                    "httpRequest": {
                        "httpMethod": "POST",
                        "url": self._url,
                        "oidcToken": {
                            "serviceAccountEmail": self._cuenta,
                            "audience": self._audiencia,
                        },
                    },
                }
            }
            respuesta = self._sesion_autenticada().post(
                f"{URL_API_TAREAS}/{self._cola}/tasks", json=cuerpo, timeout=3
            )
            if respuesta.status_code not in (200, 409):
                logger.warning("no se pudo programar el reintento: HTTP %s", respuesta.status_code)
        except Exception as error:  # programar nunca debe tumbar un cobro
            logger.warning("no se pudo programar el reintento: %s", type(error).__name__)
