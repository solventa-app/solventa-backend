"""Configuración por variables de entorno de CLAIMS (sin secretos en el código).
Nombres y valores por defecto documentados en `README.md`."""

import os
from dataclasses import dataclass

from app.application.cifrado import CIFRADO_CLAVE_LOCAL


@dataclass(frozen=True)
class Configuracion:
    mongo_uri: str
    mongo_db: str = "claims"
    # Tope de cada lectura en la réplica (HA-LAT-003): si la secundaria tarda, falla rápido.
    lectura_max_ms: int = 500
    cifrado_clave: str = CIFRADO_CLAVE_LOCAL
    kms_key_name: str = ""
    cifrado_clave_envuelta: str = ""

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        return cls(
            mongo_uri=os.getenv(
                "MONGO_URI", "mongodb://localhost:27117,localhost:27118/?replicaSet=rs0"
            ),
            mongo_db=os.getenv("MONGO_DB", "claims"),
            lectura_max_ms=int(os.getenv("LECTURA_MAX_MS", "500")),
            cifrado_clave=os.getenv("CIFRADO_CLAVE", CIFRADO_CLAVE_LOCAL),
            kms_key_name=os.getenv("KMS_KEY_NAME", "").strip(),
            cifrado_clave_envuelta=os.getenv("CIFRADO_CLAVE_ENVUELTA", "").strip(),
        )
