"""Autenticación ante los servicios privados de Cloud Run (llamadas síncronas con `httpx`).

En Cloud Run solo el BFF es público; el resto exige `roles/run.invoker` y un token de identidad en
`Authorization: Bearer` (ver `infra/servicios.tf`). El audience es el nombre del servicio destino
(`custom_audiences = [each.key]`). Copia de
`services/policy/app/application/autenticacion_servicios.py` (sin código compartido entre
servicios): el token se pide al servidor de metadatos y se cachea; dura 1 h y aquí se renueva a los
50 min.

En local (`AUTENTICACION_SERVICIOS=ninguna`, el valor por defecto) no se agrega nada."""

import os
import threading
import time
from collections.abc import Callable, Generator

import httpx

URL_METADATOS = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
VIGENCIA_SEGUNDOS = 50 * 60
MODOS = ("ninguna", "id_token")


def pedir_token_metadatos(audiencia: str) -> str:
    respuesta = httpx.get(
        URL_METADATOS,
        params={"audience": audiencia, "format": "full"},
        headers={"Metadata-Flavor": "Google"},
        timeout=2.0,
    )
    respuesta.raise_for_status()
    return respuesta.text


class AutenticacionIdToken(httpx.Auth):
    def __init__(
        self,
        audiencia: str,
        pedir_token: Callable[[str], str] = pedir_token_metadatos,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._audiencia = audiencia
        self._pedir_token = pedir_token
        self._reloj = reloj
        self._token: str | None = None
        self._expira_en = 0.0
        self._lock = threading.Lock()

    def _token_vigente(self) -> str:
        with self._lock:
            if self._token is None or self._reloj() >= self._expira_en:
                self._token = self._pedir_token(self._audiencia)
                self._expira_en = self._reloj() + VIGENCIA_SEGUNDOS
            return self._token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._token_vigente()}"
        yield request


def autenticacion_para(modo: str, audiencia: str) -> httpx.Auth | None:
    if modo == "ninguna":
        return None
    if modo == "id_token":
        return AutenticacionIdToken(audiencia)
    raise ValueError(f"AUTENTICACION_SERVICIOS desconocida: {modo}")


def modo_desde_entorno() -> str:
    modo = os.getenv("AUTENTICACION_SERVICIOS", "ninguna")
    if modo not in MODOS:
        raise ValueError(f"AUTENTICACION_SERVICIOS desconocida: {modo}")
    return modo
