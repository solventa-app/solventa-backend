"""Configuración por variables de entorno de ALMACENAMIENTO (sin secretos en el código). Nombres y
defaults documentados en `README.md`."""

import os
from dataclasses import dataclass


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
    # El bucket lo crea Terraform (`infra/almacenamiento.tf`, `crear_almacenamiento`). Vacío = el
    # servicio arranca, pero las operaciones responden 503 ALMACEN_NO_CONFIGURADO: así el despliegue
    # de todos los servicios no se cae mientras la bandera esté apagada.
    bucket: str = ""
    # "iam" en Cloud Run (signBlob con la cuenta del servicio); "local" contra fake-gcs-server.
    firmante: str = "local"
    # Cómo llega el CLIENTE al emulador (en la nube no se usa: es storage.googleapis.com).
    url_publica: str = ""
    vigencia_subida_s: int = 600
    vigencia_descarga_s: int = 300
    # Rutas completas de los temas (projects/<p>/topics/<t>); vacías = el evento se loguea.
    tema_disponible: str = ""
    tema_rechazado: str = ""

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        en_la_nube = bool(os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"))
        firmante = os.getenv("FIRMANTE", "iam" if en_la_nube else "local")
        if firmante not in ("iam", "local"):
            raise ValueError("FIRMANTE debe ser 'iam' o 'local'")
        if en_la_nube and firmante == "local":
            raise ValueError("FIRMANTE=local solo sirve contra el emulador, no en la nube")
        return cls(
            # Sin valor por defecto: compose y Cloud Run fijan DATABASE_URL completa.
            database_url=os.getenv("DATABASE_URL", ""),
            bucket=os.getenv("ALMACEN_BUCKET", "").strip(),
            firmante=firmante,
            url_publica=os.getenv("ALMACEN_URL_PUBLICA", "").strip(),
            vigencia_subida_s=_entero_positivo("URL_SUBIDA_VIGENCIA_SEGUNDOS", "600"),
            vigencia_descarga_s=_entero_positivo("URL_DESCARGA_VIGENCIA_SEGUNDOS", "300"),
            tema_disponible=os.getenv("PUBSUB_TEMA_ARCHIVO_DISPONIBLE", "").strip(),
            tema_rechazado=os.getenv("PUBSUB_TEMA_ARCHIVO_RECHAZADO", "").strip(),
        )
