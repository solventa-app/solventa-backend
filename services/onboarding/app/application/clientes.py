"""Clientes HTTP hacia ALMACENAMIENTO y el ACL Worker (servicios privados).

ONBOARDING actúa ante el storage como `SERVICIO onboarding` (`X-Actor-Rol`/`X-Actor-Id`): el
catálogo (`contracts/tipos-archivo.json`) solo deja a este servicio subir y leer los tipos
`onboarding-*`. Las imágenes van al ACL como URLs firmadas de lectura de pocos minutos: los bytes
nunca pasan por aquí.

Un error del storage que es respuesta (4xx con `detail`) se devuelve como `ErrorAlmacenamiento`
con su código; una falla de red o un 5xx es `ServicioNoDisponible`."""

from typing import Any, Protocol

import httpx

ACTOR = {"X-Actor-Rol": "SERVICIO", "X-Actor-Id": "onboarding"}


class ServicioNoDisponible(Exception):
    """El servicio no respondió. El mensaje es solo el tipo del error."""


class ErrorAlmacenamiento(Exception):
    def __init__(self, codigo: str, estado_http: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.estado_http = estado_http


class PuertoAlmacenamiento(Protocol):
    def solicitar_subida(
        self, tipo: str, tipo_contenido: str, tamano: int, propietario_id: str, consentimiento: str
    ) -> dict[str, Any]: ...

    def confirmar(self, archivo_id: str) -> dict[str, Any]: ...

    def obtener(self, archivo_id: str) -> dict[str, Any]: ...

    def url_descarga(self, archivo_id: str) -> str: ...

    def fijar_retencion(self, archivo_id: str) -> None: ...


class PuertoIdentidad(Protocol):
    def verificar(
        self, referencia: str, anverso_url: str, rostro_url: str, reverso_url: str | None
    ) -> dict[str, Any]: ...


class ClienteAlmacenamiento:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth, headers=ACTOR)

    def _pedir(self, metodo: str, ruta: str, **kwargs: Any) -> dict[str, Any]:
        try:
            respuesta = self._cliente.request(metodo, ruta, **kwargs)
        except httpx.HTTPError as error:
            raise ServicioNoDisponible(type(error).__name__) from error
        if 400 <= respuesta.status_code < 500:
            try:
                codigo = str(respuesta.json().get("detail", "SOLICITUD_INVALIDA"))
            except (ValueError, AttributeError):
                codigo = "SOLICITUD_INVALIDA"
            # El 422 de validación trae una lista: se resume en un código.
            if not codigo.isupper():
                codigo = "SOLICITUD_INVALIDA"
            raise ErrorAlmacenamiento(codigo, respuesta.status_code)
        if respuesta.status_code >= 500:
            raise ServicioNoDisponible(f"HTTP {respuesta.status_code}")
        try:
            return respuesta.json()
        except ValueError as error:
            raise ServicioNoDisponible("RespuestaIlegible") from error

    def solicitar_subida(
        self, tipo: str, tipo_contenido: str, tamano: int, propietario_id: str, consentimiento: str
    ) -> dict[str, Any]:
        return self._pedir(
            "POST",
            "/archivos/subidas",
            json={
                "tipo": tipo,
                "tipo_contenido": tipo_contenido,
                "tamano_bytes": tamano,
                "propietario_id": propietario_id,
                "consentimiento_id": consentimiento,
            },
        )

    def confirmar(self, archivo_id: str) -> dict[str, Any]:
        return self._pedir("POST", f"/archivos/{archivo_id}/confirmar")

    def obtener(self, archivo_id: str) -> dict[str, Any]:
        return self._pedir("GET", f"/archivos/{archivo_id}")

    def url_descarga(self, archivo_id: str) -> str:
        return self._pedir("GET", f"/archivos/{archivo_id}/descarga")["url"]

    def fijar_retencion(self, archivo_id: str) -> None:
        self._pedir("POST", f"/archivos/{archivo_id}/retencion")

    def cerrar(self) -> None:
        self._cliente.close()


class ClienteIdentidad:
    """`POST {ACL_URL}/identidad/verificar`: siempre 200 (aprobada, rechazada o pendiente)."""

    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def verificar(
        self, referencia: str, anverso_url: str, rostro_url: str, reverso_url: str | None
    ) -> dict[str, Any]:
        try:
            respuesta = self._cliente.post(
                "/identidad/verificar",
                json={
                    "referencia": referencia,
                    "anverso_url": anverso_url,
                    "rostro_url": rostro_url,
                    "reverso_url": reverso_url,
                },
            )
            respuesta.raise_for_status()
            return respuesta.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ServicioNoDisponible(type(error).__name__) from error

    def cerrar(self) -> None:
        self._cliente.close()
