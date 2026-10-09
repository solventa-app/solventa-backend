"""Configuración por variables de entorno del BFF (sin secretos en el código).
Nombres y defaults documentados en `README.md` y en `docs/arquitectura-backend.md`."""

import os
from dataclasses import dataclass

# Solo para local/pruebas: el repo es público, así que este valor NO es secreto.
# En Cloud Run, `CLIENTE_ID_CLAVE` llega de Secret Manager (`clave-cliente-id`).
CLAVE_CLIENTE_ID_LOCAL = "solo-local-no-secreta"
# Valor con el que Terraform crea cada secreto (infra/README.md, "Cargar los secretos").
VALOR_MARCADOR = "pendiente-de-cargar"


@dataclass(frozen=True)
class Configuracion:
    auth_url: str
    acl_url: str
    risk_url: str
    rating_url: str
    # Sin uso hasta KAN-28 (cobros): hoy `cobros`/`reintentarCobro` responden
    # "no disponible".
    payments_url: str
    acl_timeout_segundos: float
    servicios_timeout_segundos: float
    cliente_id_clave: str
    # "ninguna" en local; "id_token" en Cloud Run (servicios privados,
    # ver autenticacion_servicios.py).
    autenticacion_servicios: str

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
        return cls(
            auth_url=os.getenv("AUTH_URL", "http://localhost:8001"),
            acl_url=os.getenv("ACL_URL", "http://localhost:8005"),
            risk_url=os.getenv("RISK_URL", "http://localhost:8002"),
            rating_url=os.getenv("RATING_URL", "http://localhost:8003"),
            payments_url=os.getenv("PAYMENTS_URL", "http://localhost:8004"),
            # El ACL Worker corta a los 700 ms (EC009/EC010) y degrada; el margen
            # cubre la red y su propia respuesta degradada.
            acl_timeout_segundos=float(os.getenv("ACL_TIMEOUT_SEGUNDOS", "1.0")),
            servicios_timeout_segundos=float(os.getenv("SERVICIOS_TIMEOUT_SEGUNDOS", "2.0")),
            cliente_id_clave=clave,
            autenticacion_servicios=autenticacion,
        )
