"""Configuración por variables de entorno de ONBOARDING (sin secretos en el código). Nombres y
defaults documentados en `README.md`."""

import os
from dataclasses import dataclass

# Valor con el que `.github/servicios.json` crea los secretos antes de que el equipo los cargue.
VALOR_MARCADOR = "pendiente-de-cargar"


def _entero_positivo(nombre: str, por_defecto: str) -> int:
    try:
        valor = int(os.getenv(nombre, por_defecto))
    except ValueError:
        raise ValueError(f"{nombre} debe ser un entero") from None
    if valor <= 0:
        raise ValueError(f"{nombre} debe ser mayor que 0")
    return valor


@dataclass(frozen=True)
class Configuracion:
    database_url: str
    almacenamiento_url: str = "http://localhost:8009"
    acl_url: str = "http://localhost:8005"
    auth_url: str = "http://localhost:8001"
    # "ninguna" en local; "id_token" en Cloud Run (servicios privados).
    autenticacion_servicios: str = "ninguna"
    # Clave del HMAC del `titular_id`: la misma de AUTH y el BFF (secreto `clave-cliente-id`).
    cliente_id_clave: str = "solo-local-no-secreta"
    # Cifrado por campo de `datos_extraidos`: llave directa (local) o sobre de Cloud KMS (nube).
    cifrado_clave: str = ""
    kms_key_name: str = ""
    cifrado_clave_envuelta: str = ""
    edad_minima: int = 18
    max_intentos: int = 3
    vigencia_token_s: int = 1800
    timeout_s: float = 20.0
    # "bucle" (hilo de este proceso, local y CI) o "externo" (Cloud Tasks).
    disparador: str = "bucle"
    gcp_proyecto: str = ""
    gcp_region: str = "us-central1"
    tareas_cola: str = "verificacion-onboarding"
    tareas_cuenta: str = ""
    url_propia: str = ""
    # Rutas completas de los temas (projects/<p>/topics/<t>); vacías = el evento se loguea.
    tema_verificado: str = ""
    tema_rechazado: str = ""

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        en_la_nube = bool(os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"))
        # Como POLICY: Terraform pone DISPARADOR=externo con `crear_tareas` (infra/tareas.tf).
        disparador = os.getenv("DISPARADOR", "bucle")
        if disparador not in ("bucle", "externo"):
            raise ValueError("DISPARADOR debe ser 'bucle' o 'externo'")
        if disparador == "externo":
            faltan = [
                n for n in ("GCP_PROYECTO", "TAREAS_CUENTA", "URL_PROPIA") if not os.getenv(n)
            ]
            if faltan:
                raise ValueError(f"Con DISPARADOR=externo faltan: {', '.join(faltan)}")
        clave_hmac = os.getenv("CLIENTE_ID_CLAVE", "solo-local-no-secreta")
        if en_la_nube and clave_hmac in ("", VALOR_MARCADOR, "solo-local-no-secreta"):
            raise ValueError("CLIENTE_ID_CLAVE no está cargada (secreto clave-cliente-id)")
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            almacenamiento_url=os.getenv("ALMACENAMIENTO_URL", "http://localhost:8009"),
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            auth_url=os.getenv("AUTH_URL", "http://localhost:8001"),
            autenticacion_servicios=os.getenv("AUTENTICACION_SERVICIOS", "ninguna"),
            cliente_id_clave=clave_hmac,
            cifrado_clave=os.getenv("CIFRADO_CLAVE", ""),
            kms_key_name=os.getenv("KMS_KEY_NAME", ""),
            cifrado_clave_envuelta=os.getenv("CIFRADO_CLAVE_ENVUELTA", ""),
            edad_minima=_entero_positivo("EDAD_MINIMA", "18"),
            max_intentos=_entero_positivo("MAX_INTENTOS", "3"),
            vigencia_token_s=_entero_positivo("TOKEN_VIGENCIA_SEGUNDOS", "1800"),
            timeout_s=float(os.getenv("TIMEOUT_SEGUNDOS", "20")),
            disparador=disparador,
            gcp_proyecto=os.getenv("GCP_PROYECTO", ""),
            gcp_region=os.getenv("GCP_REGION", "us-central1"),
            tareas_cola=os.getenv("TAREAS_COLA", "verificacion-onboarding"),
            tareas_cuenta=os.getenv("TAREAS_CUENTA", ""),
            url_propia=os.getenv("URL_PROPIA", ""),
            tema_verificado=os.getenv("PUBSUB_TEMA_ONBOARDING_VERIFICADO", "").strip(),
            tema_rechazado=os.getenv("PUBSUB_TEMA_ONBOARDING_RECHAZADO", "").strip(),
        )
