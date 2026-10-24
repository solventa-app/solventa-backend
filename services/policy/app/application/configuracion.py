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
    # Decisión (UNDER) y primer cobro (PAYMENTS), W03-4. Cada uno aplica su propio corte.
    under_url: str = "http://localhost:8008"
    payments_url: str = "http://localhost:8004"
    under_timeout_segundos: float = 3.0
    payments_timeout_segundos: float = 5.0
    # Token opaco del convenio de recaudo con el banco del crédito (W03-5, REWIRE): la prima va con
    # la cuota del crédito, no con tarjeta. Nunca datos de tarjeta. Local y staging usan stubs.
    token_recaudo_credito: str = "tok_recaudo_local"
    # Quién dispara la emisión: "bucle" (hilo local, CI) o "externo" (Cloud Tasks, en Cloud Run).
    disparador: str = "bucle"
    tareas_proyecto: str = ""
    tareas_region: str = ""
    tareas_cola: str = "emision-polizas"
    tareas_cuenta: str = ""
    url_propia: str = ""
    # Cada cuánto se vuelve a consultar un primer cobro pendiente, y cuántas veces como máximo.
    cobro_pendiente_demora_segundos: float = 10.0
    emision_max_intentos: int = 12

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
        token = os.getenv("TOKEN_RECAUDO_CREDITO", "tok_recaudo_local")
        en_la_nube = bool(os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"))
        if en_la_nube and token == "tok_recaudo_local":
            raise ValueError("TOKEN_RECAUDO_CREDITO no se ha configurado")
        return cls(
            # Sin valor por defecto: compose y Cloud Run fijan DATABASE_URL completa, y una URL
            # escrita aquí llevaría la contraseña (o ninguna). `construir_pool` falla claro.
            database_url=os.getenv("DATABASE_URL", ""),
            rating_url=os.getenv("RATING_URL", "http://localhost:8003"),
            rating_timeout_segundos=float(os.getenv("POLICY_RATING_TIMEOUT_SEGUNDOS", "3.0")),
            autenticacion_servicios=modo_desde_entorno(),
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            acl_timeout_segundos=float(os.getenv("POLICY_ACL_TIMEOUT_SEGUNDOS", "3.0")),
            under_url=os.getenv("UNDER_URL", "http://localhost:8008"),
            payments_url=os.getenv("PAYMENTS_URL", "http://localhost:8004"),
            under_timeout_segundos=float(os.getenv("POLICY_UNDER_TIMEOUT_SEGUNDOS", "3.0")),
            payments_timeout_segundos=float(os.getenv("POLICY_PAYMENTS_TIMEOUT_SEGUNDOS", "5.0")),
            token_recaudo_credito=token,
            disparador=disparador,
            tareas_proyecto=os.getenv("GCP_PROYECTO", ""),
            tareas_region=os.getenv("GCP_REGION", ""),
            tareas_cola=os.getenv("TAREAS_COLA", "emision-polizas"),
            tareas_cuenta=os.getenv("TAREAS_CUENTA", ""),
            url_propia=os.getenv("URL_PROPIA", ""),
            cobro_pendiente_demora_segundos=float(
                os.getenv("COBRO_PENDIENTE_DEMORA_SEGUNDOS", "10")
            ),
            emision_max_intentos=int(os.getenv("EMISION_MAX_INTENTOS", "12")),
        )
