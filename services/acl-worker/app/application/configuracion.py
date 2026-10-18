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
    # "ninguna" en local; "id_token" en Cloud Run (los stubs de proveedores son privados).
    autenticacion_servicios: str = "ninguna"
    # Bloque 2: servir caché en TTL y presupuesto reducido con caché (EC010 <= 500 ms).
    cache_servir_en_ttl: bool = False
    presupuesto_con_cache_ms: int = 300
    # Bloque 10: presupuesto por proveedor (HA-LAT-004, EC008 <= 120 ms), acotado por timeout_s.
    presupuesto_ms_open_finance: int = 700
    presupuesto_ms_open_data: int = 700
    presupuesto_ms_pasarela: int = 700
    # Bloque 5: ruta opcional al catálogo de fuentes (contracts/fuentes.json).
    fuentes_config_path: str | None = None

    def presupuesto_proveedor_s(self, proveedor: str) -> float:
        """Devuelve el presupuesto en segundos para `proveedor`, acotado por `timeout_s`."""
        clave = proveedor.strip().lower().replace("_", "-")
        mapa_ms = {
            "open-finance": self.presupuesto_ms_open_finance,
            "open-data": self.presupuesto_ms_open_data,
            "pasarela": self.presupuesto_ms_pasarela,
        }
        ms = mapa_ms.get(clave)
        if ms is None:
            return self.timeout_s
        return min(self.timeout_s, ms / 1000)

    @property
    def presupuesto_con_cache_s(self) -> float:
        return min(self.timeout_s, self.presupuesto_con_cache_ms / 1000)

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        timeout_ms_default = os.getenv("TIMEOUT_MS", "700")
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/1"),
            open_finance_url=os.getenv("OPEN_FINANCE_URL", "http://localhost:8101"),
            open_data_url=os.getenv("OPEN_DATA_URL", "http://localhost:8102"),
            pasarela_url=os.getenv("PASARELA_URL", "http://localhost:8103"),
            timeout_s=int(timeout_ms_default) / 1000,
            breaker_umbral_fallos=int(os.getenv("BREAKER_UMBRAL_FALLOS", "3")),
            breaker_ttl_segundos=float(os.getenv("BREAKER_TTL_SEGUNDOS", "5")),
            sonda_intervalo_segundos=float(os.getenv("SONDA_INTERVALO_SEGUNDOS", "2")),
            cache_ttl_segundos=int(os.getenv("CACHE_TTL_SEGUNDOS", "300")),
            # db de Redis separado del de la caché (db 1): cola de reconciliación
            # diferida, consumida por `services/consolidador-fuentes` vía `rq`.
            cola_reconciliacion_redis_url=os.getenv(
                "COLA_RECONCILIACION_REDIS_URL", "redis://localhost:6379/2"
            ),
            autenticacion_servicios=os.getenv("AUTENTICACION_SERVICIOS", "ninguna"),
            cache_servir_en_ttl=os.getenv("CACHE_SERVIR_EN_TTL", "false").strip().lower()
            in ("1", "true", "yes", "si"),
            presupuesto_con_cache_ms=int(os.getenv("PRESUPUESTO_CON_CACHE_MS", "300")),
            presupuesto_ms_open_finance=int(
                os.getenv("PRESUPUESTO_MS_OPEN_FINANCE", timeout_ms_default)
            ),
            presupuesto_ms_open_data=int(os.getenv("PRESUPUESTO_MS_OPEN_DATA", timeout_ms_default)),
            presupuesto_ms_pasarela=int(os.getenv("PRESUPUESTO_MS_PASARELA", timeout_ms_default)),
            fuentes_config_path=os.getenv("FUENTES_CONFIG_PATH") or None,
        )
