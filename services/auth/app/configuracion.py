"""Configuración por variables de entorno de AUTH (sin secretos en el código)."""

import os
from dataclasses import dataclass

# Solo para local y pruebas: el repo es público, así que este valor NO es secreto.
# En Cloud Run, `JWT_SECRETO` llega de Secret Manager (`clave-jwt`), el mismo que usa el BFF.
JWT_SECRETO_LOCAL = "solo-local-no-secreta-jwt-solventa-0001"
# Llave de cifrado por campo, solo para local y pruebas (32 bytes en base64): el repo es público,
# así que NO es secreta. En Cloud Run llega de Secret Manager (`clave-cifrado`) o envuelta con KMS.
CIFRADO_CLAVE_LOCAL = "c29sby1sb2NhbC1ub19zZWNyZXRhLWNpZnJhZG8tMDE="
# Clave local para calcular el `titular_id` (HMAC-SHA256 del documento).
CLIENTE_ID_CLAVE_LOCAL = "dev-cliente-id-clave-local"
# Valor con el que Terraform crea cada secreto (infra/README.md, "Cargar los secretos").
VALOR_MARCADOR = "pendiente-de-cargar"
# HS256 exige al menos 32 bytes de clave (RFC 7518, sección 3.2).
LONGITUD_MINIMA_SECRETO = 32


@dataclass(frozen=True)
class Configuracion:
    database_url: str
    jwt_secreto: str
    jwt_vigencia_minutos: int
    cifrado_clave: str = CIFRADO_CLAVE_LOCAL
    kms_key_name: str | None = None
    cifrado_clave_envuelta: str | None = None
    consentimiento_vigencia_dias: int = 30
    cliente_id_clave: str = CLIENTE_ID_CLAVE_LOCAL

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        secreto = os.getenv("JWT_SECRETO", JWT_SECRETO_LOCAL)
        cifrado_clave = os.getenv("CIFRADO_CLAVE", CIFRADO_CLAVE_LOCAL)
        kms_key_name = os.getenv("KMS_KEY_NAME") or None
        cifrado_clave_envuelta = os.getenv("CIFRADO_CLAVE_ENVUELTA") or None
        consentimiento_vigencia_dias = int(os.getenv("CONSENTIMIENTO_VIGENCIA_DIAS", "30"))
        cliente_id_clave = os.getenv("CLIENTE_ID_CLAVE", CLIENTE_ID_CLAVE_LOCAL)
        # Cloud Run define `K_SERVICE` en todo contenedor: así se sabe que no es local
        # sin agregar otra variable. En la nube nunca se firma con un secreto conocido.
        if os.getenv("K_SERVICE"):
            if secreto in (JWT_SECRETO_LOCAL, VALOR_MARCADOR):
                raise ValueError("JWT_SECRETO no se ha cargado en Secret Manager")
            if len(secreto.encode("utf-8")) < LONGITUD_MINIMA_SECRETO:
                raise ValueError("JWT_SECRETO debe tener al menos 32 bytes")
            usa_sobre_kms = bool(
                kms_key_name
                and cifrado_clave_envuelta
                and cifrado_clave_envuelta != VALOR_MARCADOR
            )
            if not usa_sobre_kms and cifrado_clave in (CIFRADO_CLAVE_LOCAL, VALOR_MARCADOR):
                raise ValueError("CIFRADO_CLAVE no se ha cargado en Secret Manager")
        return cls(
            database_url=os.getenv(
                "DATABASE_URL", "postgresql://postgres:solventa-local@localhost:5432/auth"
            ),
            jwt_secreto=secreto,
            jwt_vigencia_minutos=int(os.getenv("JWT_VIGENCIA_MINUTOS", "60")),
            cifrado_clave=cifrado_clave,
            kms_key_name=kms_key_name,
            cifrado_clave_envuelta=cifrado_clave_envuelta,
            consentimiento_vigencia_dias=consentimiento_vigencia_dias,
            cliente_id_clave=cliente_id_clave,
        )
