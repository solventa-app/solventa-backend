"""Cifrado por campo de los datos sensibles de PAYMENTS (regla #6, decisión D-04).

Copiado de `services/auth/app/cifrado.py` (sin código compartido entre servicios): misma
llave (`CIFRADO_CLAVE`, secreto `clave-cifrado`), mismo formato. Hoy cifra el token de los
medios de pago; el cifrador de producción irá detrás de la misma interfaz con Cloud KMS.

AES-256-GCM con un nonce nuevo por valor. El identificador del dueño del dato viaja como dato
asociado autenticado: copiar el texto cifrado de un usuario a otro hace que el descifrado falle.
Formato guardado: base64url(nonce de 12 bytes + texto cifrado + etiqueta)."""

import base64
import binascii
import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TAMANO_NONCE = 12


class ErrorDeCifrado(Exception):
    """El valor no se pudo descifrar (llave equivocada, dato alterado o dueño distinto)."""


class CifradorCampos(Protocol):
    def cifrar(self, texto: str, dueno: str) -> str: ...

    def descifrar(self, cifrado: str, dueno: str) -> str: ...


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

    def cifrar(self, texto: str, dueno: str) -> str:
        nonce = os.urandom(TAMANO_NONCE)
        cifrado = self._aes.encrypt(nonce, texto.encode("utf-8"), dueno.encode("utf-8"))
        return base64.urlsafe_b64encode(nonce + cifrado).decode("ascii")

    def descifrar(self, cifrado: str, dueno: str) -> str:
        try:
            crudo = base64.urlsafe_b64decode(cifrado)
            texto = self._aes.decrypt(
                crudo[:TAMANO_NONCE], crudo[TAMANO_NONCE:], dueno.encode("utf-8")
            )
        except (binascii.Error, InvalidTag, ValueError) as error:
            raise ErrorDeCifrado("no se pudo descifrar el valor") from error
        return texto.decode("utf-8")
