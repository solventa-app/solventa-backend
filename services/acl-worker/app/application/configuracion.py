"""Configuración por variables de entorno del ACL Worker (sin secretos en el
código). Nombres y defaults documentados en `README.md` y en
`docs/arquitectura-backend.md`."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Configuracion:
    redis_url: str
    open_finance_url: str
    open_data_url: str
    pasarela_url: str
    timeout_s: float
    breaker_umbral_fallos: int
    breaker_ttl_segundos: float
    sonda_intervalo_segundos: float
    cache_ttl_segundos: int
    cola_reconciliacion_redis_url: str

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/1"),
            open_finance_url=os.getenv("OPEN_FINANCE_URL", "http://localhost:8101"),
            open_data_url=os.getenv("OPEN_DATA_URL", "http://localhost:8102"),
            pasarela_url=os.getenv("PASARELA_URL", "http://localhost:8103"),
            timeout_s=int(os.getenv("TIMEOUT_MS", "700")) / 1000,
            breaker_umbral_fallos=int(os.getenv("BREAKER_UMBRAL_FALLOS", "3")),
            breaker_ttl_segundos=float(os.getenv("BREAKER_TTL_SEGUNDOS", "5")),
            sonda_intervalo_segundos=float(os.getenv("SONDA_INTERVALO_SEGUNDOS", "2")),
            cache_ttl_segundos=int(os.getenv("CACHE_TTL_SEGUNDOS", "300")),
            # db de Redis separado del de la caché (db 1): cola de reconciliación
            # diferida, consumida por `services/consolidador-fuentes` vía `rq`.
            cola_reconciliacion_redis_url=os.getenv(
                "COLA_RECONCILIACION_REDIS_URL", "redis://localhost:6379/2"
            ),
        )
