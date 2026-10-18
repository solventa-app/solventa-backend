"""Cifrado por campo AES-256-GCM + cifrado de sobre con Cloud KMS (Bloque 3,
HA-SEG-002, D-04, EC022).

Mismo esquema que `services/auth/app/cifrado.py` (duplicado a propósito — regla
del repo: sin código compartido entre servicios):
- AES-256-GCM con un nonce aleatorio de 12 bytes por valor.
- Formato guardado: `base64url(nonce de 12 bytes + texto cifrado + etiqueta)`.
- En local y en CI la llave de datos llega en claro por `CIFRADO_CLAVE` (con
  valor de desarrollo `CIFRADO_CLAVE_LOCAL` porque el repositorio es público).
- En la nube (o cuando se configuran `KMS_KEY_NAME` y `CIFRADO_CLAVE_ENVUELTA`),
  el servicio desenvuelve la llave de datos al arrancar usando Cloud KMS
  (cifrado de sobre) y la mantiene solo en memoria."""

import base64
import binascii
import os
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TAMANO_NONCE = 12
CIFRADO_CLAVE_LOCAL = "c29sby1sb2NhbC1ub19zZWNyZXRhLWNpZnJhZG8tMDE="
VALOR_MARCADOR = "pendiente-de-cargar"


class ErrorDeCifrado(Exception):
    """El valor no se pudo descifrar o la llave de sobre en KMS no está disponible."""


class CifradorCampos(Protocol):
    def cifrar(self, texto: str, dueno: str = "") -> str: ...

    def descifrar(self, cifrado: str, dueno: str = "") -> str: ...


class ClienteKms(Protocol):
    def desenvolver(self, nombre_llave: str, clave_envuelta: bytes) -> bytes: ...


def desenvolver_clave_kms(
    nombre_llave: str, clave_envuelta_b64: str, cliente_kms: Any = None
) -> bytes:
    """Desenvuelve `CIFRADO_CLAVE_ENVUELTA` con Cloud KMS (o el cliente inyectado).
    Si la versión de la llave en KMS está deshabilitada o no hay permiso, lanza
    `ErrorDeCifrado`."""
    try:
        clave_envuelta = base64.urlsafe_b64decode(clave_envuelta_b64)
    except (binascii.Error, ValueError) as error:
        raise ErrorDeCifrado("CIFRADO_CLAVE_ENVUELTA no es base64 válido") from error

    try:
        if cliente_kms is not None:
            if callable(cliente_kms):
                llave = cliente_kms(nombre_llave, clave_envuelta)
            elif hasattr(cliente_kms, "desenvolver"):
                llave = cliente_kms.desenvolver(nombre_llave, clave_envuelta)
            elif hasattr(cliente_kms, "decrypt"):
                respuesta = cliente_kms.decrypt(
                    request={"name": nombre_llave, "ciphertext": clave_envuelta}
                )
                llave = (
                    respuesta["plaintext"]
                    if isinstance(respuesta, dict)
                    else respuesta.plaintext
                )
            else:
                raise ErrorDeCifrado("cliente_kms no soporta desenvolver/decrypt")
        else:
            from google.cloud import kms  # type: ignore[import-untyped]

            cliente_real = kms.KeyManagementServiceClient()
            respuesta = cliente_real.decrypt(
                request={"name": nombre_llave, "ciphertext": clave_envuelta}
            )
            llave = respuesta.plaintext
    except ErrorDeCifrado:
        raise
    except Exception as error:
        raise ErrorDeCifrado(
            f"No se pudo desenvolver la llave de datos con KMS ({nombre_llave})"
        ) from error

    if isinstance(llave, str):
        try:
            llave = base64.urlsafe_b64decode(llave)
        except (binascii.Error, ValueError):
            llave = llave.encode("utf-8")
    return bytes(llave)


class CifradorAesGcm:
    def __init__(self, llave: bytes) -> None:
        if len(llave) != 32:
            raise ValueError("La llave de cifrado debe tener 32 bytes")
        self._aes = AESGCM(llave)

    @classmethod
    def desde_base64(cls, llave_b64: str) -> "CifradorAesGcm":
        try:
            return cls(base64.urlsafe_b64decode(llave_b64))
        except (binascii.Error, ValueError) as error:
            raise ValueError("CIFRADO_CLAVE debe ser 32 bytes en base64") from error

    @classmethod
    def desde_kms(
        cls,
        nombre_llave: str,
        clave_envuelta_b64: str,
        cliente_kms: Any = None,
    ) -> "CifradorAesGcm":
        llave = desenvolver_clave_kms(nombre_llave, clave_envuelta_b64, cliente_kms)
        try:
            return cls(llave)
        except ValueError as error:
            raise ErrorDeCifrado(str(error)) from error

    @classmethod
    def desde_entorno(
        cls,
        cifrado_clave: str | None = None,
        kms_key_name: str | None = None,
        cifrado_clave_envuelta: str | None = None,
        cliente_kms: Any = None,
    ) -> "CifradorAesGcm":
        llave_kms = (
            kms_key_name
            if kms_key_name is not None
            else os.getenv("KMS_KEY_NAME", "").strip()
        )
        sobre_b64 = (
            cifrado_clave_envuelta
            if cifrado_clave_envuelta is not None
            else os.getenv("CIFRADO_CLAVE_ENVUELTA", "").strip()
        )
        if llave_kms and sobre_b64:
            return cls.desde_kms(llave_kms, sobre_b64, cliente_kms=cliente_kms)

        clave = (
            cifrado_clave
            if cifrado_clave is not None
            else os.getenv("CIFRADO_CLAVE", CIFRADO_CLAVE_LOCAL)
        )
        if os.getenv("K_SERVICE") and clave in (CIFRADO_CLAVE_LOCAL, VALOR_MARCADOR):
            raise ValueError("CIFRADO_CLAVE no se ha cargado en Secret Manager")
        return cls.desde_base64(clave)

    def cifrar(self, texto: str, dueno: str = "") -> str:
        nonce = os.urandom(TAMANO_NONCE)
        aad = dueno.encode("utf-8") if dueno else None
        cifrado = self._aes.encrypt(nonce, texto.encode("utf-8"), aad)
        return base64.urlsafe_b64encode(nonce + cifrado).decode("ascii")

    def descifrar(self, cifrado: str, dueno: str = "") -> str:
        try:
            crudo = base64.urlsafe_b64decode(cifrado)
            if len(crudo) <= TAMANO_NONCE:
                raise ErrorDeCifrado("texto cifrado truncado")
            aad = dueno.encode("utf-8") if dueno else None
            texto = self._aes.decrypt(crudo[:TAMANO_NONCE], crudo[TAMANO_NONCE:], aad)
        except (binascii.Error, InvalidTag, ValueError) as error:
            raise ErrorDeCifrado("no se pudo descifrar el valor") from error
        return texto.decode("utf-8")
