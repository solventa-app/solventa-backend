"""Cifrado de los documentos firmados (KAN-26, T6, decisión D-1 del plan de la revisión QA).

El contrato firmado y la póliza se guardan en ALMACENAMIENTO **cifrados por POLICY antes de
subirlos**: el bucket (con su propia CMEK) nunca ve el PDF en claro.

- **Una llave de datos por documento** (AES-256-GCM, nonce nuevo). Cifra el PDF y se guarda en
  POLICY **envuelta**: por Cloud KMS en la nube (la misma llave `campos-personales` del sobre de
  D-04) y, en local o sin KMS, con la llave de desarrollo `CIFRADO_CLAVE`. La llave de datos en
  claro solo existe en memoria, mientras se cifra o se descifra.
- **Dato asociado autenticado:** `<propuesta_id>:<tipo>`. Tanto el cifrado del PDF como el
  envoltorio de la llave lo llevan, así que copiar un contenedor (o una llave envuelta) a otra
  propuesta o a otro tipo de documento hace que el descifrado falle. La etiqueta GCM se verifica
  siempre: un documento alterado nunca se sirve.
- **Contenedor:** `MAGIA` (8 bytes, `SOLVENC1`) + versión (1 byte) + nonce (12) + texto cifrado
  con su etiqueta. ALMACENAMIENTO solo acepta `application/octet-stream` si empieza por `MAGIA`,
  así que un PDF en claro no entra por esta puerta (`servicio_archivos._firma_coincide`).
- **Huella:** el SHA-256 del PDF en claro se guarda con el documento y se compara al descifrar.
"""

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.application.cifrado import clave_desde_entorno

# Igual que `MAGIA_CONTENEDOR_CIFRADO` de ALMACENAMIENTO (sin código compartido entre servicios).
MAGIA = b"SOLVENC1"
VERSION = 1
TAMANO_NONCE = 12
TAMANO_LLAVE = 32
TIPO_CONTENIDO = "application/octet-stream"


class ErrorDeSobre(Exception):
    """El documento no se pudo descifrar: contenedor mal formado, llave o dueño distintos, o
    contenido alterado. Es una falla de integridad, no de disponibilidad."""


class EnvolvedorLlaves(Protocol):
    def envolver(self, llave: bytes, contexto: str) -> bytes: ...

    def desenvolver(self, envuelta: bytes, contexto: str) -> bytes: ...


class EnvolvedorLocal:
    """Envuelve la llave de datos con una llave fija (AES-256-GCM). Es la de desarrollo en local y,
    en la nube sin KMS configurado, la `CIFRADO_CLAVE` de Secret Manager."""

    def __init__(self, llave_maestra: bytes) -> None:
        if len(llave_maestra) != TAMANO_LLAVE:
            raise ValueError("La llave maestra debe tener 32 bytes")
        self._aes = AESGCM(llave_maestra)

    @classmethod
    def desde_base64(cls, llave_b64: str) -> "EnvolvedorLocal":
        try:
            return cls(base64.urlsafe_b64decode(llave_b64))
        except (binascii.Error, ValueError) as error:
            raise ValueError("CIFRADO_CLAVE debe ser 32 bytes en base64") from error

    def envolver(self, llave: bytes, contexto: str) -> bytes:
        nonce = os.urandom(TAMANO_NONCE)
        return nonce + self._aes.encrypt(nonce, llave, contexto.encode("utf-8"))

    def desenvolver(self, envuelta: bytes, contexto: str) -> bytes:
        try:
            return self._aes.decrypt(
                envuelta[:TAMANO_NONCE], envuelta[TAMANO_NONCE:], contexto.encode("utf-8")
            )
        except (InvalidTag, ValueError) as error:
            raise ErrorDeSobre("no se pudo desenvolver la llave de datos") from error


class EnvolvedorKms:
    """Envuelve la llave de datos con Cloud KMS (`encrypt` y `decrypt` de la llave simétrica
    `campos-personales`), con el dueño como dato asociado. Si la versión de la llave está
    deshabilitada o no hay permiso, KMS falla: el documento no se puede leer (EC022), y eso es una
    falla de disponibilidad, no de integridad."""

    def __init__(self, nombre_llave: str, cliente: Any = None) -> None:
        self._nombre = nombre_llave
        self._cliente = cliente

    def _kms(self) -> Any:
        if self._cliente is None:
            from google.cloud import kms  # type: ignore[import-untyped]

            self._cliente = kms.KeyManagementServiceClient()
        return self._cliente

    def envolver(self, llave: bytes, contexto: str) -> bytes:
        respuesta = self._kms().encrypt(
            request={
                "name": self._nombre,
                "plaintext": llave,
                "additional_authenticated_data": contexto.encode("utf-8"),
            }
        )
        return bytes(respuesta.ciphertext)

    def desenvolver(self, envuelta: bytes, contexto: str) -> bytes:
        respuesta = self._kms().decrypt(
            request={
                "name": self._nombre,
                "ciphertext": envuelta,
                "additional_authenticated_data": contexto.encode("utf-8"),
            }
        )
        return bytes(respuesta.plaintext)


def envolvedor_desde_entorno() -> EnvolvedorLlaves:
    """Con `KMS_KEY_NAME` (la nube con KMS) envuelve con Cloud KMS; si no, con `CIFRADO_CLAVE`: la
    llave pública de pruebas en local, o la de Secret Manager si aún no hay KMS."""
    nombre = os.getenv("KMS_KEY_NAME", "").strip()
    if nombre:
        return EnvolvedorKms(nombre)
    return EnvolvedorLocal.desde_base64(clave_desde_entorno())


@dataclass(frozen=True)
class DocumentoCifrado:
    contenedor: bytes
    clave_envuelta: str  # base64url de la llave de datos envuelta
    sha256: str  # huella del PDF en claro
    tamano_bytes: int  # del PDF en claro


def contexto_de(propuesta_id: str, tipo: str) -> str:
    return f"{propuesta_id}:{tipo}"


def cifrar_documento(
    pdf: bytes, propuesta_id: str, tipo: str, envolvedor: EnvolvedorLlaves
) -> DocumentoCifrado:
    contexto = contexto_de(propuesta_id, tipo)
    llave = os.urandom(TAMANO_LLAVE)
    nonce = os.urandom(TAMANO_NONCE)
    cifrado = AESGCM(llave).encrypt(nonce, pdf, contexto.encode("utf-8"))
    envuelta = envolvedor.envolver(llave, contexto)
    return DocumentoCifrado(
        contenedor=MAGIA + bytes([VERSION]) + nonce + cifrado,
        clave_envuelta=base64.urlsafe_b64encode(envuelta).decode("ascii"),
        sha256=hashlib.sha256(pdf).hexdigest(),
        tamano_bytes=len(pdf),
    )


def descifrar_documento(
    contenedor: bytes,
    clave_envuelta: str,
    propuesta_id: str,
    tipo: str,
    envolvedor: EnvolvedorLlaves,
    sha256_esperado: str | None = None,
) -> bytes:
    """El PDF en claro, o `ErrorDeSobre` si no es el contenedor de esta propuesta y tipo, si algo
    se alteró o si la huella guardada no coincide. Las fallas de KMS (disponibilidad) suben tal
    cual."""
    encabezado = len(MAGIA) + 1
    if len(contenedor) < encabezado + TAMANO_NONCE or not contenedor.startswith(MAGIA):
        raise ErrorDeSobre("el contenedor no es un documento cifrado de Solventa")
    if contenedor[len(MAGIA)] != VERSION:
        raise ErrorDeSobre("versión de contenedor desconocida")
    contexto = contexto_de(propuesta_id, tipo)
    try:
        envuelta = base64.urlsafe_b64decode(clave_envuelta)
    except (binascii.Error, ValueError) as error:
        raise ErrorDeSobre("la llave envuelta no es válida") from error
    llave = envolvedor.desenvolver(envuelta, contexto)
    nonce = contenedor[encabezado : encabezado + TAMANO_NONCE]
    try:
        pdf = AESGCM(llave).decrypt(
            nonce, contenedor[encabezado + TAMANO_NONCE :], contexto.encode("utf-8")
        )
    except (InvalidTag, ValueError) as error:
        raise ErrorDeSobre(
            "el documento no se pudo descifrar (alterado o de otro dueño)"
        ) from error
    if sha256_esperado is not None and hashlib.sha256(pdf).hexdigest() != sha256_esperado:
        raise ErrorDeSobre("la huella del documento no coincide")
    return pdf
