"""Configuración por variables de entorno del servicio `avisos` (sin secretos en el código)."""

import os
from dataclasses import dataclass

ORIGENES_LOCALES = "http://localhost:5173,http://localhost:8080"


@dataclass(frozen=True)
class Configuracion:
    # Misma base de Redis que el BFF (que guarda los tickets) y POLICY (que publica los avisos).
    redis_url: str
    # Orígenes del front que pueden abrir el stream (`Origin` de `EventSource`).
    origenes_permitidos: frozenset[str]
    latido_segundos: float

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        origenes = os.getenv("ORIGENES_PERMITIDOS", ORIGENES_LOCALES)
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/3"),
            origenes_permitidos=frozenset(
                o.strip().rstrip("/") for o in origenes.split(",") if o.strip()
            ),
            latido_segundos=float(os.getenv("LATIDO_SEGUNDOS", "25")),
        )
