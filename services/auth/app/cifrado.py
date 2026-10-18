"""Cifrado por campo de los datos personales de AUTH (regla #6, decisión D-04 y bloque 3).

Soporta dos modos detrás de la misma interfaz `CifradorCampos`:
- **Cifrado de sobre (nube):** la llave de datos (DEK de 32 bytes) se guarda en Secret Manager
  envuelta por la llave de Cloud KMS (`KMS_KEY_NAME` + `CIFRADO_CLAVE_ENVUELTA`). Al arrancar,
  AUTH la desenvuelve con KMS y la mantiene solo en memoria (`CifradorAesGcm.desde_sobre_kms`).
  Rotar la llave de KMS no obliga a recifrar los datos, y deshabilitar la versión de KMS impide
  arrancar o descifrar (HA-SEG-002 / EC022).
- **Llave directa (local y CI):** llega por variable de entorno (`CIFRADO_CLAVE`, 32 bytes base64).

AES-256-GCM con un nonce nuevo por valor. El identificador del dueño del dato viaja como dato
asociado autenticado: copiar el texto cifrado de un usuario a otro hace que el descifrado falle.
Formato guardado: base64url(nonce de 12 bytes + texto cifrado + etiqueta)."""

import base64
import binascii
import os
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.configuracion import Configuracion

TAMANO_NONCE = 12


class ErrorDeCifrado(Exception):
    """El valor no se pudo descifrar o la llave de cifrado no se pudo desenvolver con KMS."""


# Alias para compatibilidad entre módulos y pruebas (bloque 3 / EC022).
ErrorCifrado = ErrorDeCifrado


class CifradorCampos(Protocol):
    def cifrar(self, texto: str, dueno: str) -> str: ...

    def descifrar(self, cifrado: str, dueno: str) -> str: ...


class DesenvolvedorKms(Protocol):
    """Puerto para desenvolver una DEK cifrada (envelope encryption) con Cloud KMS."""

    def desenvolver(self, clave_envuelta_b64: str) -> bytes: ...


class DesenvolvedorGoogleKms:
    """Desenvuelve la DEK usando `google.cloud.kms` bajo demanda."""

    def __init__(self, kms_key_name: str, cliente: Any | None = None) -> None:
        if not kms_key_name or not kms_key_name.strip():
            raise ErrorDeCifrado("KMS_KEY_NAME es obligatorio para desenvolver la llave")
        self._kms_key_name = kms_key_name.strip()
        self._cliente = cliente

    def desenvolver(self, clave_envuelta_b64: str) -> bytes:
        try:
            cifrado = base64.urlsafe_b64decode(clave_envuelta_b64.strip())
        except (binascii.Error, ValueError) as error:
            raise ErrorDeCifrado("CIFRADO_CLAVE_ENVUELTA no es base64 válido") from error
        if not cifrado:
            raise ErrorDeCifrado("CIFRADO_CLAVE_ENVUELTA está vacía")

        cliente = self._cliente
        if cliente is None:
            try:
                from google.cloud import kms  # type: ignore[import-not-found]

                cliente = kms.KeyManagementServiceClient()
            except ImportError as error:
                raise ErrorDeCifrado(
                    "google-cloud-kms no está instalado para desenvolver la llave con KMS"
                ) from error
            except Exception as error:
                raise ErrorDeCifrado(
                    f"No se pudo inicializar el cliente de Cloud KMS: {error}"
                ) from error

        try:
            respuesta = cliente.decrypt(
                request={"name": self._kms_key_name, "ciphertext": cifrado}
            )
        except Exception as error:
            raise ErrorDeCifrado(
                f"No se pudo desenvolver la llave con KMS ({self._kms_key_name}): {error}"
            ) from error

        dek = getattr(respuesta, "plaintext", None)
        if not isinstance(dek, (bytes, bytearray)) or not dek:
            raise ErrorDeCifrado("KMS devolvió una llave de datos vacía o inválida")
        return bytes(dek)


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
    def desde_sobre_kms(
        cls, clave_envuelta_b64: str, desenvolvedor: DesenvolvedorKms
    ) -> "CifradorAesGcm":
        if not clave_envuelta_b64 or not clave_envuelta_b64.strip():
            raise ErrorDeCifrado("CIFRADO_CLAVE_ENVUELTA está vacía")
        try:
            dek = desenvolvedor.desenvolver(clave_envuelta_b64.strip())
        except ErrorDeCifrado:
            raise
        except Exception as error:
            raise ErrorDeCifrado(f"No se pudo desenvolver la DEK con KMS: {error}") from error
        try:
            return cls(dek)
        except ValueError as error:
            raise ErrorDeCifrado("La DEK desenvuelta por KMS debe tener 32 bytes") from error

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


def construir_cifrador(
    config: Configuracion, desenvolvedor: DesenvolvedorKms | None = None
) -> CifradorAesGcm:
    """Construye el cifrador AES-256-GCM: si `kms_key_name` y `cifrado_clave_envuelta` están
    configurados, desenvuelve la DEK al arrancar con Cloud KMS y la deja solo en memoria;
    en local y CI usa `CIFRADO_CLAVE` en base64."""
    if config.kms_key_name and config.cifrado_clave_envuelta:
        kms_desenvolvedor = desenvolvedor or DesenvolvedorGoogleKms(config.kms_key_name)
        return CifradorAesGcm.desde_sobre_kms(config.cifrado_clave_envuelta, kms_desenvolvedor)
    return CifradorAesGcm.desde_base64(config.cifrado_clave)
