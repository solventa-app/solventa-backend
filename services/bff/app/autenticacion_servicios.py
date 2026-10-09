"""Autenticación del BFF ante los servicios privados de Cloud Run.

En Cloud Run solo el BFF es público; el resto exige `roles/run.invoker` y un
token de identidad en `Authorization: Bearer` (ver `infra/servicios.tf`). El
audience es el nombre del servicio (`custom_audiences = [each.key]`), el mismo
que usa la prueba de humo de `deploy-staging.yml`.

El token se pide al servidor de metadatos de Cloud Run directamente con
`httpx` (sin `google-auth`, que necesitaría `requests` como transporte) y se
cachea: un token de identidad dura 1 h y aquí se renueva a los 50 min.

En local (`AUTENTICACION_SERVICIOS=ninguna`) no se agrega nada."""

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx

URL_METADATOS = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
VIGENCIA_SEGUNDOS = 50 * 60


async def pedir_token_metadatos(audiencia: str) -> str:
    async with httpx.AsyncClient(timeout=2.0) as cliente:
        respuesta = await cliente.get(
            URL_METADATOS,
            params={"audience": audiencia, "format": "full"},
            headers={"Metadata-Flavor": "Google"},
        )
        respuesta.raise_for_status()
        return respuesta.text


class AutenticacionIdToken(httpx.Auth):
    def __init__(
        self,
        audiencia: str,
        pedir_token: Callable[[str], Awaitable[str]] = pedir_token_metadatos,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._audiencia = audiencia
        self._pedir_token = pedir_token
        self._reloj = reloj
        self._token: str | None = None
        self._expira_en = 0.0
        self._lock = asyncio.Lock()

    async def _token_vigente(self) -> str:
        async with self._lock:
            if self._token is None or self._reloj() >= self._expira_en:
                self._token = await self._pedir_token(self._audiencia)
                self._expira_en = self._reloj() + VIGENCIA_SEGUNDOS
            return self._token

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self._token_vigente()}"
        yield request


def autenticacion_para(modo: str, audiencia: str) -> httpx.Auth | None:
    if modo == "ninguna":
        return None
    if modo == "id_token":
        return AutenticacionIdToken(audiencia)
    raise ValueError(f"AUTENTICACION_SERVICIOS desconocida: {modo}")
