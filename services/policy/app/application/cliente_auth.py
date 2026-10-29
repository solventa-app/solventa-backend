"""Cliente HTTP hacia AUTH, para el aviso de póliza emitida (HU-W03, bloque 8, W03-16 y W03-17).

POLICY no guarda ningún dato personal del cliente. Para avisarle, le pide a AUTH (servicio a
servicio, AUTH es privado) su contacto (`GET {AUTH_URL}/usuarios/{id}/contacto`) y su nombre
(`POST {AUTH_URL}/usuarios/nombres`), los usa para armar el aviso y el PDF y los descarta: **nada
se guarda ni se escribe en logs** (regla de arquitectura #6).

Si AUTH no responde **no se asume que el cliente no tiene contacto**: se lanza `AuthNoDisponible` y
el aviso se reintenta. Un `404` **con el mensaje de AUTH** sí es una respuesta: el usuario no
existe o no es CLIENTE. Cualquier otro `404` (p. ej. un AUTH que aún no tiene la ruta, en un
despliegue gradual) NO lo es: omitir el aviso es permanente, así que ante la duda se reintenta."""

from dataclasses import dataclass
from typing import Protocol

import httpx

# El detalle con el que AUTH responde 404 cuando el usuario no existe o no es CLIENTE
# (`services/auth/app/main.py`); una ruta desconocida responde `Not Found`.
DETALLE_USUARIO_NO_ENCONTRADO = "usuario no encontrado"


class AuthNoDisponible(Exception):
    """AUTH no respondió (red, timeout, 5xx o una respuesta que no se entiende). El mensaje es solo
    el tipo del error: nunca la respuesta, que lleva datos personales."""


@dataclass
class Contacto:
    correo: str | None = None
    telefono: str | None = None

    @property
    def vacio(self) -> bool:
        return not self.correo and not self.telefono


class PuertoContactos(Protocol):
    def contacto_de(self, usuario_id: str) -> Contacto | None:
        """El contacto del cliente, o `None` si AUTH no lo conoce. Lanza `AuthNoDisponible`."""
        ...

    def nombre_de(self, usuario_id: str) -> str | None:
        """El nombre del cliente, o `None` si no tiene. Lanza `AuthNoDisponible`."""
        ...


class PuertoConsentimientos(Protocol):
    def consentimiento_vigente(self, consentimiento_id: str) -> bool:
        """Si el consentimiento sigue vigente (W03-26). Uno que no existe no lo está. Lanza
        `AuthNoDisponible`."""
        ...


class ClienteAuth:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def contacto_de(self, usuario_id: str) -> Contacto | None:
        try:
            respuesta = self._cliente.get(f"/usuarios/{usuario_id}/contacto")
            if respuesta.status_code == 404:
                if respuesta.json().get("detail") == DETALLE_USUARIO_NO_ENCONTRADO:
                    return None
                raise AuthNoDisponible("RutaNoEncontrada")
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            return Contacto(correo=cuerpo.get("correo"), telefono=cuerpo.get("telefono"))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise AuthNoDisponible(type(error).__name__) from error

    def nombre_de(self, usuario_id: str) -> str | None:
        try:
            # El id va en el cuerpo, como lo hace el BFF.
            respuesta = self._cliente.post("/usuarios/nombres", json={"ids": [usuario_id]})
            respuesta.raise_for_status()
            return respuesta.json().get(usuario_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise AuthNoDisponible(type(error).__name__) from error

    def consentimiento_vigente(self, consentimiento_id: str) -> bool:
        try:
            respuesta = self._cliente.get(f"/consentimientos/{consentimiento_id}")
            if respuesta.status_code == 404:
                return False
            respuesta.raise_for_status()
            return bool(respuesta.json()["vigente"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise AuthNoDisponible(type(error).__name__) from error

    def cerrar(self) -> None:
        self._cliente.close()
