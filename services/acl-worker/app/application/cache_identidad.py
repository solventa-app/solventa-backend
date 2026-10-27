"""Caché del resultado de la verificación de identidad (HU-M01): cada verificación en Didit se
cobra, y un reintento del cliente con las MISMAS imágenes no debe pagarse dos veces.

- **Clave:** SHA-256 de las imágenes (anverso, reverso y rostro). Nunca la cédula ni otro dato
  personal: unas imágenes distintas son otra verificación aunque sea la misma persona.
- **Valor:** el resultado completo, con los datos extraídos, cifrado con AES-256-GCM (la clave de
  la caché viaja como dato asociado: un valor copiado a otra clave no se descifra).
- **TTL:** 30 días por defecto, igual que la retención de las imágenes de onboarding.
- Solo se guardan resultados definitivos (`aprobada` o `rechazada`); un `pendiente` nunca.
- Cache-aside: un fallo de Redis o una llave distinta se comporta como "no hay caché"."""

import base64
import hashlib
import json
import logging
import os
from dataclasses import asdict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.application.cache import PuertoAlmacenClaveValor
from app.domain.puertos import DatosDocumento, ImagenesIdentidad, ResultadoIdentidad

logger = logging.getLogger("acl_worker.identidad")

TAMANO_NONCE = 12
TTL_POR_DEFECTO_S = 30 * 24 * 3600


def huella_imagenes(imagenes: ImagenesIdentidad) -> str:
    partes = [imagenes.anverso, imagenes.reverso or b"", imagenes.rostro]
    return hashlib.sha256(b"".join(hashlib.sha256(parte).digest() for parte in partes)).hexdigest()


class CacheIdentidad:
    def __init__(
        self, almacen: PuertoAlmacenClaveValor, llave: bytes, ttl_segundos: int = TTL_POR_DEFECTO_S
    ) -> None:
        if len(llave) != 32:
            raise ValueError("la llave de la caché de identidad debe tener 32 bytes")
        self._almacen = almacen
        self._aes = AESGCM(llave)
        self._ttl_segundos = ttl_segundos

    @staticmethod
    def _clave(huella: str) -> str:
        return f"acl:identidad:{huella}"

    def guardar(self, huella: str, resultado: ResultadoIdentidad) -> None:
        if resultado.estado == "pendiente":
            return
        clave = self._clave(huella)
        nonce = os.urandom(TAMANO_NONCE)
        texto = json.dumps(asdict(resultado)).encode("utf-8")
        cifrado = nonce + self._aes.encrypt(nonce, texto, clave.encode("utf-8"))
        try:
            self._almacen.set(
                clave, base64.urlsafe_b64encode(cifrado).decode("ascii"), ex=self._ttl_segundos
            )
        except Exception as error:
            logger.warning("no se pudo guardar en caché error=%s", type(error).__name__)

    def obtener(self, huella: str) -> ResultadoIdentidad | None:
        clave = self._clave(huella)
        try:
            crudo = self._almacen.get(clave)
            if crudo is None:
                return None
            cifrado = base64.urlsafe_b64decode(crudo)
            texto = self._aes.decrypt(
                cifrado[:TAMANO_NONCE], cifrado[TAMANO_NONCE:], clave.encode("utf-8")
            )
        except Exception as error:
            logger.warning("caché de identidad ilegible error=%s", type(error).__name__)
            return None
        cuerpo = json.loads(texto)
        datos = cuerpo.pop("datos")
        cuerpo["alertas"] = tuple(cuerpo["alertas"])
        return ResultadoIdentidad(
            **cuerpo, datos=DatosDocumento(**datos) if datos is not None else None
        )
