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
    # Quién dispara el reintento de pendientes: "bucle" (local y CI) o "externo" (Cloud Tasks, en
    # Cloud Run).
    disparador: str = "bucle"
    # Pasado este tiempo en `cobrando`, un cobro se considera atascado (el proceso se cayó a mitad).
    minutos_cobro_atascado: float = 2.0
    # Solo con DISPARADOR=externo (los fija Terraform): dónde está la cola y quién firma las tareas.
    tareas_proyecto: str = ""
    tareas_region: str = ""
    tareas_cola: str = "reintento-cobros"
    tareas_cuenta: str = ""
    url_propia: str = ""
    # W05-A7: cada cuántos días se cobra una póliza nueva, y dónde se publican los avisos (W05-A6).
    cobro_recurrente_intervalo_dias: int = 30
    redis_url: str = ""

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        disparador = os.getenv("DISPARADOR", "bucle")
        if disparador not in ("bucle", "externo"):
            raise ValueError("DISPARADOR debe ser 'bucle' o 'externo'")
        if disparador == "externo":
            faltan = [
                nombre
                for nombre in ("GCP_PROYECTO", "GCP_REGION", "TAREAS_CUENTA", "URL_PROPIA")
                if not os.getenv(nombre)
            ]
            if faltan:
                raise ValueError(f"DISPARADOR=externo exige {', '.join(faltan)}")
        intervalo = int(os.getenv("COBRO_RECURRENTE_INTERVALO_DIAS", "30"))
        if not 1 <= intervalo <= 31:
            raise ValueError("COBRO_RECURRENTE_INTERVALO_DIAS debe estar entre 1 y 31")
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
            # Con el bucle local: cada cuántos segundos se revisan los cobros `pendiente`. Con
            # DISPARADOR=externo: cuánto espera Cloud Tasks entre una pasada y la siguiente.
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
            disparador=disparador,
            minutos_cobro_atascado=float(os.getenv("COBRO_ATASCADO_MINUTOS", "2")),
            tareas_proyecto=os.getenv("GCP_PROYECTO", ""),
            tareas_region=os.getenv("GCP_REGION", ""),
            tareas_cola=os.getenv("TAREAS_COLA", "reintento-cobros"),
            tareas_cuenta=os.getenv("TAREAS_CUENTA", ""),
            url_propia=os.getenv("URL_PROPIA", ""),
            cobro_recurrente_intervalo_dias=intervalo,
            redis_url=os.getenv("REDIS_URL", ""),
        )
