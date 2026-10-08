"""Configuración por variables de entorno de RISK (sin secretos en el código).
Nombres y defaults documentados en `README.md` y en `docs/arquitectura-backend.md`."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Configuracion:
    mongo_uri: str
    acl_url: str

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
        )
