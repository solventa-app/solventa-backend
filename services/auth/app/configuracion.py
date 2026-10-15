"""Configuración por variables de entorno de AUTH (sin secretos en el código)."""

import os
from dataclasses import dataclass

# Solo para local y pruebas: el repo es público, así que este valor NO es secreto.
# En Cloud Run, `JWT_SECRETO` llega de Secret Manager (`clave-jwt`), el mismo que usa el BFF.
JWT_SECRETO_LOCAL = "solo-local-no-secreta-jwt-solventa-0001"
# Valor con el que Terraform crea cada secreto (infra/README.md, "Cargar los secretos").
VALOR_MARCADOR = "pendiente-de-cargar"
# HS256 exige al menos 32 bytes de clave (RFC 7518, sección 3.2).
LONGITUD_MINIMA_SECRETO = 32


@dataclass(frozen=True)
class Configuracion:
    database_url: str
    jwt_secreto: str
    jwt_vigencia_minutos: int

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        secreto = os.getenv("JWT_SECRETO", JWT_SECRETO_LOCAL)
        # Cloud Run define `K_SERVICE` en todo contenedor: así se sabe que no es local
        # sin agregar otra variable. En la nube nunca se firma con un secreto conocido.
        if os.getenv("K_SERVICE"):
            if secreto in (JWT_SECRETO_LOCAL, VALOR_MARCADOR):
                raise ValueError("JWT_SECRETO no se ha cargado en Secret Manager")
            if len(secreto.encode("utf-8")) < LONGITUD_MINIMA_SECRETO:
                raise ValueError("JWT_SECRETO debe tener al menos 32 bytes")
        return cls(
            database_url=os.getenv(
                "DATABASE_URL", "postgresql://postgres:solventa-local@localhost:5432/auth"
            ),
            jwt_secreto=secreto,
            jwt_vigencia_minutos=int(os.getenv("JWT_VIGENCIA_MINUTOS", "60")),
        )
