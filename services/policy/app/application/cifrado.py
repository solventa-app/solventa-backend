"""Cifrado por campo de los datos financieros de la propuesta (HA-SEG-002, EC022, decisión D-04).

La prima y la cobertura de la oferta aceptada se guardan cifradas en la base (AES-256-GCM, un
nonce nuevo por valor), no en claro. Copia de `services/payments/app/application/cifrado.py` (sin
código compartido entre servicios). La llave
llega por variable de entorno (`CIFRADO_CLAVE`, 32 bytes en base64); en la nube viene de Secret
Manager y, cuando se use el cifrado de sobre con Cloud KMS (como en AUTH), el cifrador de producción
irá detrás de la misma interfaz sin tocar a quien lo usa.

El identificador de la propuesta viaja como dato asociado autenticado: copiar el texto cifrado de
una propuesta a otra hace que el descifrado falle.

Formato guardado: `enc:v1:` + base64url(nonce de 12 bytes + texto cifrado + etiqueta)."""

import base64
import binascii
import os
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TAMANO_NONCE = 12
PREFIJO = "enc:v1:"
# Solo para local y pruebas (32 bytes en base64): el repo es público, así que NO es secreta.
# En Cloud Run llega de Secret Manager (`clave-cifrado`, la misma de AUTH).
CIFRADO_CLAVE_LOCAL = "c29sby1sb2NhbC1ub19zZWNyZXRhLWNpZnJhZG8tMDE="
# Valor con el que Terraform crea cada secreto (infra/README.md, "Cargar los secretos").
VALOR_MARCADOR = "pendiente-de-cargar"


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
        if texto.startswith(PREFIJO):
            return texto  # ya cifrado: cifrar dos veces no lo envuelve otra vez
        nonce = os.urandom(TAMANO_NONCE)
        cifrado = self._aes.encrypt(nonce, texto.encode("utf-8"), dueno.encode("utf-8"))
        return PREFIJO + base64.urlsafe_b64encode(nonce + cifrado).decode("ascii")

    def descifrar(self, cifrado: str, dueno: str) -> str:
        if not cifrado.startswith(PREFIJO):
            raise ErrorDeCifrado("el valor no está cifrado")
        try:
            crudo = base64.urlsafe_b64decode(cifrado[len(PREFIJO) :])
            texto = self._aes.decrypt(
                crudo[:TAMANO_NONCE], crudo[TAMANO_NONCE:], dueno.encode("utf-8")
            )
        except (binascii.Error, InvalidTag, ValueError) as error:
            raise ErrorDeCifrado("no se pudo descifrar el valor") from error
        return texto.decode("utf-8")


def cifrador_desde_entorno() -> CifradorAesGcm:
    """Local: la llave pública de pruebas. En la nube (Cloud Run define `K_SERVICE`, los jobs
    `CLOUD_RUN_JOB`) la llave es obligatoria y no puede ser la local ni el marcador de Terraform."""
    clave = os.getenv("CIFRADO_CLAVE", CIFRADO_CLAVE_LOCAL)
    en_la_nube = bool(os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"))
    if en_la_nube and clave in (CIFRADO_CLAVE_LOCAL, VALOR_MARCADOR):
        raise ValueError("CIFRADO_CLAVE no se ha cargado en Secret Manager")
    return CifradorAesGcm.desde_base64(clave)
