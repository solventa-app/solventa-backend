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
    almacenamiento_url: str = "http://localhost:8009"
    policy_url: str = "http://localhost:8007"
    servicios_timeout_segundos: float = 5.0
    # En local la URL firmada apunta a `localhost:4443`; desde el contenedor es `fake-gcs:4443`.
    almacen_reescritura_origenes: str = ""
    # Solo local (W04-15): `POST /_demo/reiniciar` vuelve a la semilla de `SEMILLA_DIR`.
    demo_reiniciar: bool = False
    semilla_dir: str = "/semilla"

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
            almacenamiento_url=os.getenv("ALMACENAMIENTO_URL", "http://localhost:8009"),
            policy_url=os.getenv("POLICY_URL", "http://localhost:8007"),
            servicios_timeout_segundos=float(os.getenv("SERVICIOS_TIMEOUT_SEGUNDOS", "5")),
            almacen_reescritura_origenes=os.getenv("ALMACEN_REESCRITURA_ORIGENES", ""),
            demo_reiniciar=os.getenv("DEMO_REINICIAR", "false").strip().lower() == "true",
            semilla_dir=os.getenv("SEMILLA_DIR", "/semilla"),
        )
