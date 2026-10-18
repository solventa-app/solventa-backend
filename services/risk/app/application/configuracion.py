"""Configuración por variables de entorno de RISK (sin secretos en el código).
Nombres y defaults documentados en `README.md` y en `docs/arquitectura-backend.md`."""

import os
from dataclasses import dataclass

from app.application.cifrado import CIFRADO_CLAVE_LOCAL


@dataclass(frozen=True)
class Configuracion:
    mongo_uri: str
    acl_url: str
    redis_url: str = "redis://localhost:6379/0"
    score_cache_ttl_segundos: int = 300
    cifrado_clave: str = CIFRADO_CLAVE_LOCAL
    kms_key_name: str = ""
    cifrado_clave_envuelta: str = ""
    pubsub_tema_perfil_actualizado: str = ""
    batch_tamano_lote: int = 500

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        return cls(
            mongo_uri=os.getenv(
                "MONGO_URI", "mongodb://localhost:27117,localhost:27118/?replicaSet=rs0"
            ),
            # No se usa en esta tarea (RISK recibe los datos de las fuentes ya
            # resueltos en el cuerpo de `POST /perfiles`, no llama al ACL
            # Worker directamente); se mantiene por compatibilidad con la
            # variable que ya fija `docker-compose.yml` para este servicio.
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            score_cache_ttl_segundos=int(os.getenv("SCORE_CACHE_TTL_SEGUNDOS", "300")),
            cifrado_clave=os.getenv("CIFRADO_CLAVE", CIFRADO_CLAVE_LOCAL),
            kms_key_name=os.getenv("KMS_KEY_NAME", "").strip(),
            cifrado_clave_envuelta=os.getenv("CIFRADO_CLAVE_ENVUELTA", "").strip(),
            pubsub_tema_perfil_actualizado=os.getenv(
                "PUBSUB_TEMA_PERFIL_ACTUALIZADO", ""
            ).strip(),
            batch_tamano_lote=int(os.getenv("BATCH_TAMANO_LOTE", "500")),
        )

