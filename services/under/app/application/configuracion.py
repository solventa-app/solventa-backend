"""Configuración por variables de entorno de UNDER (sin secretos en el código). Nombres y defaults
documentados en `README.md`."""

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.application.autenticacion_servicios import modo_desde_entorno


@dataclass(frozen=True)
class Configuracion:
    acl_url: str
    acl_timeout_segundos: float
    # "ninguna" en local; "id_token" en Cloud Run (el ACL Worker es privado).
    autenticacion_servicios: str
    # Por encima de esta cobertura (COP) la suscripción no se aprueba sola: va a revisión manual.
    cobertura_maxima_automatica: Decimal = Decimal("500000000")

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        try:
            maxima = Decimal(os.getenv("COBERTURA_MAXIMA_AUTOMATICA", "500000000"))
        except InvalidOperation as error:
            raise ValueError("COBERTURA_MAXIMA_AUTOMATICA debe ser un número decimal") from error
        return cls(
            # El ACL Worker corta al proveedor a los 700 ms; este timeout cubre que el propio ACL
            # esté inalcanzable.
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            acl_timeout_segundos=float(os.getenv("UNDER_ACL_TIMEOUT_SEGUNDOS", "3.0")),
            autenticacion_servicios=modo_desde_entorno(),
            cobertura_maxima_automatica=maxima,
        )
