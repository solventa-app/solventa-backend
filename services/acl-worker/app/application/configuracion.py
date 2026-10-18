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
    # Qué adaptador de pasarela se usa (EC031): "estandar" o "alterna". Cambiar de proveedor es
    # esto.
    proveedor_pasarela: str = "estandar"
    # Quién dispara la sonda: "bucle" (local y CI) o "externo" (Cloud Scheduler, en Cloud Run).
    disparador: str = "bucle"
    # Solo con DISPARADOR=externo (los fija Terraform): la cola de reconciliación de Cloud Tasks y a
    # llama (el consolidador), con qué cuenta se firma la tarea.
    tareas_proyecto: str = ""
    tareas_region: str = ""
    tareas_cola_reconciliacion: str = "reconciliacion-fuentes"
    tareas_cuenta: str = ""
    consolidador_url: str = ""

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        proveedor_pasarela = os.getenv("PROVEEDOR_PASARELA", "estandar")
        if proveedor_pasarela not in ("estandar", "alterna"):
            raise ValueError("PROVEEDOR_PASARELA debe ser 'estandar' o 'alterna'")
        disparador = os.getenv("DISPARADOR", "bucle")
        if disparador not in ("bucle", "externo"):
            raise ValueError("DISPARADOR debe ser 'bucle' o 'externo'")
        if disparador == "externo":
            faltan = [
                nombre
                for nombre in ("GCP_PROYECTO", "GCP_REGION", "TAREAS_CUENTA", "CONSOLIDADOR_URL")
                if not os.getenv(nombre)
            ]
            if faltan:
                raise ValueError(f"DISPARADOR=externo exige {', '.join(faltan)}")
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
            autenticacion_servicios=os.getenv("AUTENTICACION_SERVICIOS", "ninguna"),
            proveedor_pasarela=proveedor_pasarela,
            disparador=disparador,
            tareas_proyecto=os.getenv("GCP_PROYECTO", ""),
            tareas_region=os.getenv("GCP_REGION", ""),
            tareas_cola_reconciliacion=os.getenv(
                "TAREAS_COLA_RECONCILIACION", "reconciliacion-fuentes"
            ),
            tareas_cuenta=os.getenv("TAREAS_CUENTA", ""),
            consolidador_url=os.getenv("CONSOLIDADOR_URL", ""),
        )
