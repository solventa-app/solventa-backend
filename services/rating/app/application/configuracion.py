"""Configuración por variables de entorno de RATING (sin secretos en el
código). Nombres y defaults documentados en `README.md` y en
`docs/arquitectura-backend.md`."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Configuracion:
    mongo_uri: str
    redis_url: str
    max_time_ms: int

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        return cls(
            mongo_uri=os.getenv(
                "MONGO_URI", "mongodb://localhost:27117,localhost:27118/?replicaSet=rs0"
            ),
            # Se recibe (ya está en docker-compose.yml) pero el Cache-Aside de
            # scores (HA-LAT-001) es T-W01-6 completo y queda fuera de
            # alcance de esta tarea — ver README.md.
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            # Cota de espera de la sesión causal sobre la secundaria (D-02,
            # EC011/EC012: p95 <=150ms, p99 <=300ms de presupuesto total). Si
            # se agota, se cae a leer de la PRIMARIA en la misma solicitud.
            max_time_ms=int(os.getenv("RATING_MAX_TIME_MS", "150")),
        )
