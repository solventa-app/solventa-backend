"""Configuración por variables de entorno del BFF (sin secretos en el código).
Nombres y defaults documentados en `README.md` y en `docs/arquitectura-backend.md`."""

import os
from dataclasses import dataclass

# Solo para local/pruebas: el repo es público, así que este valor NO es secreto.
# En Cloud Run, `CLIENTE_ID_CLAVE` llega de Secret Manager (`clave-cliente-id`).
CLAVE_CLIENTE_ID_LOCAL = "solo-local-no-secreta"
# Valor con el que Terraform crea cada secreto (infra/README.md, "Cargar los secretos").
VALOR_MARCADOR = "pendiente-de-cargar"
# Mismo valor local que AUTH (services/auth/app/configuracion.py): firma y verificación
# usan el mismo secreto (HS256). En Cloud Run llega de Secret Manager (`clave-jwt`).
JWT_SECRETO_LOCAL = "solo-local-no-secreta-jwt-solventa-0001"
# HS256 exige al menos 32 bytes de clave (RFC 7518, sección 3.2).
LONGITUD_MINIMA_SECRETO = 32


@dataclass(frozen=True)
class Configuracion:
    auth_url: str
    acl_url: str
    risk_url: str
    rating_url: str
    # Cobros del back-office (KAN-28): `cobros` y `reintentarCobro`.
    payments_url: str
    # Propuestas y pólizas (HU-W03): `aceptarOferta`, `propuesta`.
    policy_url: str
    # Archivos (KAN-250): `solicitarSubidaArchivo`, `archivo`, `descargarArchivo`.
    almacenamiento_url: str
    # Tickets de un solo uso del stream de avisos (W03-37): se guardan 60 s en Redis.
    redis_url: str
    # URL pública del stream SSE del servicio `avisos` (fuera del API Gateway), sin el ticket.
    avisos_url: str
    # Origen del front web: a él regresa el titular desde la página de pago de la pasarela
    # (`<url_front>/pago/retorno/<propuestaId>?sesion=…`). Debe estar en la lista blanca de la
    # pasarela (`URLS_RETORNO_PERMITIDAS`).
    url_front: str
    # Onboarding del móvil (HU-M01, contrato v0.20).
    onboarding_url: str
    acl_timeout_segundos: float
    servicios_timeout_segundos: float
    cliente_id_clave: str
    # "ninguna" en local; "id_token" en Cloud Run (servicios privados,
    # ver autenticacion_servicios.py).
    autenticacion_servicios: str
    jwt_secreto: str
    # "opcional" | "obligatoria": ver app/autenticacion_usuarios.py.
    autenticacion_usuarios: str
    # Mesa de siniestros (HU-W04): `siniestros`, `aprobarSiniestro`...
    claims_url: str = "http://localhost:8012"
    # Salud del sistema (HU-W07, bloque 6): el BFF solo les pide `/health` a UNDER y al
    # consolidador.
    under_url: str = "http://localhost:8008"
    consolidador_url: str = "http://localhost:8006"
    # "ninguna" (CI y pruebas: solo `/health`), "prometheus" (local) o "monitoring" (Cloud Run).
    metricas_fuente: str = "ninguna"
    prometheus_url: str = "http://localhost:9190"
    gcp_proyecto: str = ""
    # Sesión web (T22): el refresh viaja en una cookie `httpOnly; SameSite=Strict`. `Secure` queda
    # activo salvo que se apague (`COOKIE_REFRESH_SECURE=false`, solo para HTTP local sin TLS).
    cookie_refresh_secure: bool = True
    # Orígenes del front web autorizados a renovar la sesión (`ORIGENES_WEB_PERMITIDOS`, separados
    # por coma). Vacío = sin verificación de `Origin` (local); en staging y producción se fija.
    origenes_web: tuple[str, ...] = ()

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        autenticacion = os.getenv("AUTENTICACION_SERVICIOS", "ninguna")
        clave = os.getenv("CLIENTE_ID_CLAVE")
        if clave is None:
            if autenticacion != "ninguna":
                # En la nube nunca se cae a la clave local conocida.
                raise ValueError("falta CLIENTE_ID_CLAVE")
            clave = CLAVE_CLIENTE_ID_LOCAL
        if autenticacion != "ninguna" and clave in (CLAVE_CLIENTE_ID_LOCAL, VALOR_MARCADOR):
            # Terraform crea el secreto con un marcador; sin el valor real no se arranca.
            raise ValueError("CLIENTE_ID_CLAVE no se ha cargado en Secret Manager")
        jwt_secreto = os.getenv("JWT_SECRETO", JWT_SECRETO_LOCAL)
        if autenticacion != "ninguna":
            if jwt_secreto in (JWT_SECRETO_LOCAL, VALOR_MARCADOR):
                raise ValueError("JWT_SECRETO no se ha cargado en Secret Manager")
            if len(jwt_secreto.encode("utf-8")) < LONGITUD_MINIMA_SECRETO:
                raise ValueError("JWT_SECRETO debe tener al menos 32 bytes")
        modo = os.getenv("AUTENTICACION_USUARIOS", "opcional")
        if modo not in ("opcional", "obligatoria"):
            raise ValueError(f"AUTENTICACION_USUARIOS desconocida: {modo}")
        return cls(
            auth_url=os.getenv("AUTH_URL", "http://localhost:8001"),
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            risk_url=os.getenv("RISK_URL", "http://localhost:8002"),
            rating_url=os.getenv("RATING_URL", "http://localhost:8003"),
            payments_url=os.getenv("PAYMENTS_URL", "http://localhost:8004"),
            policy_url=os.getenv("POLICY_URL", "http://localhost:8007"),
            almacenamiento_url=os.getenv("ALMACENAMIENTO_URL", "http://localhost:8009"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            avisos_url=os.getenv("AVISOS_URL", "http://localhost:8011/avisos/stream"),
            url_front=os.getenv("URL_FRONT", "http://localhost:5173").rstrip("/"),
            onboarding_url=os.getenv("ONBOARDING_URL", "http://localhost:8010"),
            claims_url=os.getenv("CLAIMS_URL", "http://localhost:8012"),
            under_url=os.getenv("UNDER_URL", "http://localhost:8008"),
            consolidador_url=os.getenv("CONSOLIDADOR_URL", "http://localhost:8006"),
            metricas_fuente=os.getenv("METRICAS_FUENTE", "ninguna"),
            prometheus_url=os.getenv("PROMETHEUS_URL", "http://localhost:9190"),
            gcp_proyecto=os.getenv("GCP_PROYECTO", os.getenv("GOOGLE_CLOUD_PROJECT", "")),
            # El ACL Worker corta a los 700 ms (EC009/EC010) y degrada; el margen
            # cubre la red y su propia respuesta degradada.
            acl_timeout_segundos=float(os.getenv("ACL_TIMEOUT_SEGUNDOS", "1.0")),
            servicios_timeout_segundos=float(os.getenv("SERVICIOS_TIMEOUT_SEGUNDOS", "2.0")),
            cliente_id_clave=clave,
            autenticacion_servicios=autenticacion,
            jwt_secreto=jwt_secreto,
            autenticacion_usuarios=modo,
            cookie_refresh_secure=os.getenv("COOKIE_REFRESH_SECURE", "true").lower() != "false",
            origenes_web=tuple(
                o.strip().rstrip("/")
                for o in os.getenv("ORIGENES_WEB_PERMITIDOS", "").split(",")
                if o.strip()
            ),
        )
