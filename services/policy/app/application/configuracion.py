"""Configuración por variables de entorno de POLICY (sin secretos en el código). Nombres y defaults
documentados en `README.md`."""

import os
from dataclasses import dataclass

from app.application.autenticacion_servicios import modo_desde_entorno


@dataclass(frozen=True)
class Configuracion:
    database_url: str
    rating_url: str
    rating_timeout_segundos: float
    # "ninguna" en local; "id_token" en Cloud Run (RATING es privado).
    autenticacion_servicios: str
    # La firma pasa por el ACL Worker, nunca directo al proveedor (regla #2). El ACL corta a los
    # 700 ms; este timeout cubre que el propio ACL esté inalcanzable.
    acl_url: str = "http://localhost:8005"
    acl_timeout_segundos: float = 3.0

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        return cls(
            # Sin valor por defecto: compose y Cloud Run fijan DATABASE_URL completa, y una URL
            # escrita aquí llevaría la contraseña (o ninguna). `construir_pool` falla claro.
            database_url=os.getenv("DATABASE_URL", ""),
            rating_url=os.getenv("RATING_URL", "http://localhost:8003"),
            rating_timeout_segundos=float(os.getenv("POLICY_RATING_TIMEOUT_SEGUNDOS", "3.0")),
            autenticacion_servicios=modo_desde_entorno(),
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            acl_timeout_segundos=float(os.getenv("POLICY_ACL_TIMEOUT_SEGUNDOS", "3.0")),
        )
