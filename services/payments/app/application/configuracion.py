"""Configuración por variables de entorno de PAYMENTS (sin secretos en el
código). Nombres y defaults documentados en `README.md`."""

import os
from dataclasses import dataclass

from app.application.autenticacion_servicios import modo_desde_entorno


@dataclass(frozen=True)
class Configuracion:
    database_url: str
    acl_url: str
    acl_timeout_segundos: float
    reintento_intervalo_segundos: float
    reintento_max_intentos: int
    reintentos_rechazo_maximo: int
    # "ninguna" en local; "id_token" en Cloud Run (el ACL Worker es privado).
    autenticacion_servicios: str

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        return cls(
            # Sin valor por defecto: compose y Cloud Run fijan DATABASE_URL completa, y una URL
            # escrita aquí llevaría la contraseña (o ninguna). `construir_pool` falla claro.
            database_url=os.getenv("DATABASE_URL", ""),
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            # Timeout de PAYMENTS llamando al ACL Worker (defensa en profundidad):
            # el presupuesto duro de 700 ms proveedor-abajo ya lo aplica el ACL
            # Worker; este timeout cubre que el propio ACL Worker esté
            # inalcanzable por red.
            acl_timeout_segundos=float(os.getenv("PAYMENTS_ACL_TIMEOUT_SEGUNDOS", "3.0")),
            # Placeholder local de Cloud Tasks (T-W05-4, ver app/application/reintento.py):
            # cada cuántos segundos se revisan los cobros `pendiente`.
            reintento_intervalo_segundos=float(
                os.getenv("REINTENTO_COBRO_INTERVALO_SEGUNDOS", "5")
            ),
            # Tope de reintentos por cobro: agotados, queda `pendiente` para
            # seguimiento manual (no se reintenta para siempre).
            reintento_max_intentos=int(os.getenv("REINTENTO_COBRO_MAX_INTENTOS", "10")),
            # Reintentos manuales de un cobro `rechazado` (CA-W05-02). 3 es el número del
            # prototipo (W05-01): agotados, el cobro requiere otro medio de pago.
            reintentos_rechazo_maximo=int(os.getenv("REINTENTOS_RECHAZO_MAXIMO", "3")),
            autenticacion_servicios=modo_desde_entorno(),
        )
