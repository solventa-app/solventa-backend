"""Códigos, tokens y sus huellas (HU-W03, W03-20 y W03-22).

POLICY nunca guarda el código de verificación ni el token del enlace: solo su huella HMAC-SHA256.
Quien robe la base no puede usarlos, y un código de 6 dígitos no se adivina offline sin la clave.
La clave sale de `CIFRADO_CLAVE` (la misma del cifrado por campo), derivada con un prefijo propio
para no reutilizar la llave tal cual."""

import hashlib
import hmac
import secrets

LARGO_CODIGO = 6


def clave_de_huellas(material: str) -> bytes:
    return hashlib.sha256(b"policy-huellas:" + material.encode("utf-8")).digest()


def huella(valor: str, clave: bytes) -> str:
    return hmac.new(clave, valor.encode("utf-8"), hashlib.sha256).hexdigest()


def coincide(valor: str, huella_guardada: str, clave: bytes) -> bool:
    return hmac.compare_digest(huella(valor, clave), huella_guardada)


def generar_codigo() -> str:
    return f"{secrets.randbelow(10**LARGO_CODIGO):0{LARGO_CODIGO}d}"


def generar_token() -> str:
    """Token del enlace: 32 bytes al azar, apto para el fragmento de una URL."""
    return secrets.token_urlsafe(32)


def enmascarar_correo(correo: str) -> str:
    """`camila.rojas@correo.com` -> `c•••@correo.com`: nunca el correo completo."""
    usuario, _, dominio = correo.partition("@")
    return f"{usuario[:1]}•••@{dominio}"
