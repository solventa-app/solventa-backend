"""Clientes HTTP del BFF hacia AUTH, ACL Worker, RISK y RATING.

Cada `Protocol` permite probar el BFF con dobles (ver `tests/fakes.py`) sin
levantar los servicios. Las fallas de red, timeouts y 5xx se convierten en
`ErrorServicio`; quien orquesta decide cómo degradar (regla #4: nunca un 5xx
crudo al cliente). Ningún cuerpo de petición ni de respuesta se loguea."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.autenticacion_servicios import autenticacion_para
from app.configuracion import Configuracion


class ErrorServicio(Exception):
    """El servicio interno no respondió a tiempo o respondió con error."""


class SolicitudInvalida(Exception):
    """El servicio rechazó la solicitud (4xx): reintentar no la arregla."""


class PuertoAuth(Protocol):
    async def registrar_consentimiento(self, finalidad: str, version_texto: str) -> dict: ...

    async def obtener_consentimiento(self, consentimiento_id: str) -> dict | None: ...

    async def revocar_consentimiento(self, consentimiento_id: str) -> dict | None: ...


class PuertoAcl(Protocol):
    async def consultar_fuente(
        self, cliente_id: str, consentimiento_id: str, fuente: str
    ) -> dict: ...


class PuertoRisk(Protocol):
    async def registrar_perfil(self, cliente_id: str, fuentes: list[dict]) -> dict: ...


class PuertoRating(Protocol):
    async def generar_oferta(
        self, cliente_id: str, operation_time: str, cobertura: str
    ) -> dict: ...


@dataclass
class Clientes:
    auth: PuertoAuth
    acl: PuertoAcl
    risk: PuertoRisk
    rating: PuertoRating


async def _pedir(
    cliente: httpx.AsyncClient, metodo: str, ruta: str, cuerpo: dict | None = None
) -> httpx.Response:
    try:
        respuesta = await cliente.request(metodo, ruta, json=cuerpo)
    except httpx.HTTPError as error:
        raise ErrorServicio(type(error).__name__) from error
    if respuesta.status_code >= 500:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta


def _json_o_none(respuesta: httpx.Response) -> dict[str, Any] | None:
    """404 = no existe; cualquier otro 4xx es un error de integración, no del usuario."""
    if respuesta.status_code == 404:
        return None
    if respuesta.is_error:
        raise ErrorServicio(f"HTTP {respuesta.status_code}")
    return respuesta.json()


class ClienteAuthHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def registrar_consentimiento(self, finalidad: str, version_texto: str) -> dict:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/consentimientos",
            {"finalidad": finalidad, "version_texto": version_texto},
        )
        cuerpo = _json_o_none(respuesta)
        if cuerpo is None:
            raise ErrorServicio("HTTP 404")
        return cuerpo

    async def obtener_consentimiento(self, consentimiento_id: str) -> dict | None:
        return _json_o_none(
            await _pedir(self._cliente, "GET", f"/consentimientos/{consentimiento_id}")
        )

    async def revocar_consentimiento(self, consentimiento_id: str) -> dict | None:
        return _json_o_none(
            await _pedir(self._cliente, "POST", f"/consentimientos/{consentimiento_id}/revocar")
        )


class ClienteAclHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def consultar_fuente(self, cliente_id: str, consentimiento_id: str, fuente: str) -> dict:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/fuentes/consultar",
            {"cliente_id": cliente_id, "consentimiento_id": consentimiento_id, "fuente": fuente},
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


class ClienteRiskHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def registrar_perfil(self, cliente_id: str, fuentes: list[dict]) -> dict:
        respuesta = await _pedir(
            self._cliente, "POST", "/perfiles", {"cliente_id": cliente_id, "fuentes": fuentes}
        )
        if respuesta.is_error:
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


class ClienteRatingHttp:
    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self._cliente = cliente

    async def generar_oferta(self, cliente_id: str, operation_time: str, cobertura: str) -> dict:
        respuesta = await _pedir(
            self._cliente,
            "POST",
            "/ofertas",
            {"cliente_id": cliente_id, "operation_time": operation_time, "cobertura": cobertura},
        )
        if respuesta.status_code == 400:
            raise SolicitudInvalida(respuesta.json().get("detail", ""))
        if respuesta.is_error:
            # 404 incluido: RISK acaba de escribir el perfil, así que no encontrarlo
            # es un problema de lectura (réplica), no del usuario.
            raise ErrorServicio(f"HTTP {respuesta.status_code}")
        return respuesta.json()


def construir_clientes(config: Configuracion) -> tuple[Clientes, list[httpx.AsyncClient]]:
    """Devuelve los clientes y los `httpx.AsyncClient` abiertos, para cerrarlos al apagar."""

    def http(url: str, audiencia: str, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=url,
            timeout=timeout,
            auth=autenticacion_para(config.autenticacion_servicios, audiencia),
        )

    auth = http(config.auth_url, "auth", config.servicios_timeout_segundos)
    acl = http(config.acl_url, "acl-worker", config.acl_timeout_segundos)
    risk = http(config.risk_url, "risk", config.servicios_timeout_segundos)
    rating = http(config.rating_url, "rating", config.servicios_timeout_segundos)
    clientes = Clientes(
        auth=ClienteAuthHttp(auth),
        acl=ClienteAclHttp(acl),
        risk=ClienteRiskHttp(risk),
        rating=ClienteRatingHttp(rating),
    )
    return clientes, [auth, acl, risk, rating]
