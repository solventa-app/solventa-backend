"""Configuración por variables de entorno del ACL Worker (sin secretos en el
código). Nombres y defaults documentados en `README.md` y en
`docs/arquitectura-backend.md`."""

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

PREFIJO_PRESUPUESTO = "PRESUPUESTO_MS_"


def _normalizar_proveedor(nombre: str) -> str:
    """`OPEN_FINANCE`, `open_finance` y `open-finance` son el mismo proveedor."""
    return nombre.strip().lower().replace("_", "-")


def _presupuestos_desde_entorno() -> dict[str, int]:
    """Lee todo `PRESUPUESTO_MS_<PROVEEDOR>` del entorno: un proveedor nuevo no obliga a tocar el
    código, basta con su variable."""
    presupuestos: dict[str, int] = {}
    for nombre, valor in os.environ.items():
        if not nombre.startswith(PREFIJO_PRESUPUESTO):
            continue
        try:
            ms = int(valor)
        except ValueError:
            raise ValueError(f"{nombre} debe ser un entero en milisegundos") from None
        if ms <= 0:
            raise ValueError(f"{nombre} debe ser mayor que 0")
        presupuestos[_normalizar_proveedor(nombre.removeprefix(PREFIJO_PRESUPUESTO))] = ms
    return presupuestos


# Terraform crea cada secreto con este valor hasta que alguien carga el real.
VALOR_MARCADOR = "pendiente-de-cargar"


def _llave_cache_identidad(valor: str) -> bytes | None:
    valor = valor.strip()
    if not valor or valor == VALOR_MARCADOR:
        return None
    try:
        llave = base64.b64decode(valor, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("IDENTIDAD_LLAVE_CACHE no es base64 válido") from error
    if len(llave) != 32:
        raise ValueError("IDENTIDAD_LLAVE_CACHE debe tener 32 bytes")
    return llave


@dataclass(frozen=True)
class Configuracion:
    redis_url: str
    open_finance_url: str
    open_data_url: str
    pasarela_url: str
    timeout_s: float
    breaker_umbral_fallos: int
    breaker_ttl_segundos: float
    sonda_intervalo_segundos: float
    cache_ttl_segundos: int
    cola_reconciliacion_redis_url: str
    # "ninguna" en local; "id_token" en Cloud Run (los stubs de proveedores son privados).
    autenticacion_servicios: str = "ninguna"
    # Qué adaptador de pasarela se usa (EC031): "estandar" o "alterna". Cambiar de proveedor es
    # esto.
    proveedor_pasarela: str = "estandar"
    # Firma electrónica (HU-W03): dónde está y qué adaptador se usa (EC031, igual que la pasarela).
    firma_url: str = "http://localhost:8104"
    proveedor_firma: str = "estandar"
    # KYC (HU-W03): dónde está el proveedor de verificación de identidad.
    kyc_url: str = "http://localhost:8105"
    # Verificación de identidad del onboarding móvil (HU-M01): Didit en staging, `stubs/kyc` (que
    # imita las rutas de Didit) en local y CI. Corre en segundo plano, así que su corte es largo y
    # no lo acota `timeout_s` (700 ms es para el camino del usuario).
    identidad_url: str = "http://localhost:8105"
    identidad_api_key: str = "local"
    identidad_timeout_s: float = 15.0
    # De dónde acepta el ACL bajar imágenes (el bucket de archivos): nadie lo usa de proxy.
    identidad_origenes_imagenes: tuple[str, ...] = (
        "https://storage.googleapis.com/",
        "http://localhost:4443/",
        "http://fake-gcs:4443/",
    )
    # Llave AES-256 (base64) de la caché cifrada del resultado. Sin ella: en local se genera una por
    # proceso; en la nube la caché queda apagada (cada verificación se paga, pero nada se rompe).
    identidad_llave_cache: bytes | None = None
    # Solo local: `origen=destino` para bajar del emulador las URLs firmadas para el celular.
    identidad_reescrituras: Mapping[str, str] = field(default_factory=dict)
    identidad_cache_ttl_segundos: int = 30 * 24 * 3600
    # Notificaciones al cliente (HU-W03, bloque 5).
    notificaciones_url: str = "http://localhost:8106"
    # Tope del contenido de un archivo adjunto a un aviso (bytes ya decodificados). El PDF de una
    # póliza pesa pocos KB; el tope evita que por aquí viaje cualquier cosa.
    adjunto_maximo_bytes: int = 5_000_000
    # Quién dispara la sonda: "bucle" (local y CI) o "externo" (Cloud Scheduler, en Cloud Run).
    disparador: str = "bucle"
    # Solo con DISPARADOR=externo (los fija Terraform): la cola de reconciliación de Cloud Tasks y a
    # llama (el consolidador), con qué cuenta se firma la tarea.
    tareas_proyecto: str = ""
    tareas_region: str = ""
    tareas_cola_reconciliacion: str = "reconciliacion-fuentes"
    tareas_cuenta: str = ""
    consolidador_url: str = ""

    # Bloque 2: servir caché en TTL y presupuesto reducido con caché (EC010 <= 500 ms).
    cache_servir_en_ttl: bool = False
    presupuesto_con_cache_ms: int = 300
    # Bloque 10: presupuesto por proveedor en ms (HA-LAT-004, EC008 <= 120 ms), acotado por
    # timeout_s. Sale del entorno como `PRESUPUESTO_MS_<PROVEEDOR>`; sin variable, vale `timeout_s`.
    presupuestos_ms: Mapping[str, int] = field(default_factory=dict)
    # Ruta síncrona del usuario (CA-W03-05): lo que el usuario espera mientras el proveedor responde
    # tiene que caber en el plazo del gateway (HA-LAT-004, `infra/gateway/*`), descontando lo que
    # cuestan los saltos BFF -> POLICY -> ACL. A los proveedores de `proveedores_ruta_sincrona`
    # se les acota el presupuesto a ese techo; los demás (el cobro, el KYC y el aviso corren en
    # segundo plano) no. Medido en local: ~80 ms de saltos; la reserva deja margen para la nube.
    plazo_ruta_sincrona_ms: int = 700
    reserva_saltos_ms: int = 250
    proveedores_ruta_sincrona: tuple[str, ...] = ("firma",)
    # Bloque 5: ruta opcional al catálogo de fuentes (contracts/fuentes.json).
    fuentes_config_path: str | None = None

    @property
    def techo_ruta_sincrona_s(self) -> float:
        return (self.plazo_ruta_sincrona_ms - self.reserva_saltos_ms) / 1000

    def presupuesto_proveedor_s(self, proveedor: str) -> float:
        """Devuelve el presupuesto en segundos para `proveedor`: el suyo, acotado por `timeout_s` y,
        si el proveedor está en la ruta síncrona del usuario, por el techo de esa ruta."""
        clave = _normalizar_proveedor(proveedor)
        ms = self.presupuestos_ms.get(clave)
        presupuesto_s = self.timeout_s if ms is None else min(self.timeout_s, ms / 1000)
        if clave in self.proveedores_ruta_sincrona:
            presupuesto_s = min(presupuesto_s, self.techo_ruta_sincrona_s)
        return presupuesto_s

    @property
    def presupuesto_con_cache_s(self) -> float:
        return min(self.timeout_s, self.presupuesto_con_cache_ms / 1000)

    @classmethod
    def desde_entorno(cls) -> "Configuracion":
        timeout_ms_default = os.getenv("TIMEOUT_MS", "700")
        proveedor_pasarela = os.getenv("PROVEEDOR_PASARELA", "estandar")
        if proveedor_pasarela not in ("estandar", "alterna"):
            raise ValueError("PROVEEDOR_PASARELA debe ser 'estandar' o 'alterna'")
        proveedor_firma = os.getenv("PROVEEDOR_FIRMA", "estandar")
        if proveedor_firma not in ("estandar", "alterna"):
            raise ValueError("PROVEEDOR_FIRMA debe ser 'estandar' o 'alterna'")
        disparador = os.getenv("DISPARADOR", "bucle")
        if disparador not in ("bucle", "externo"):
            raise ValueError("DISPARADOR debe ser 'bucle' o 'externo'")
        plazo_ruta_sincrona_ms = int(os.getenv("PLAZO_RUTA_SINCRONA_MS", "700"))
        reserva_saltos_ms = int(os.getenv("RESERVA_SALTOS_MS", "250"))
        if not 0 <= reserva_saltos_ms < plazo_ruta_sincrona_ms:
            raise ValueError("RESERVA_SALTOS_MS debe ser menor que PLAZO_RUTA_SINCRONA_MS")
        if disparador == "externo":
            faltan = [
                nombre
                for nombre in ("GCP_PROYECTO", "GCP_REGION", "TAREAS_CUENTA", "CONSOLIDADOR_URL")
                if not os.getenv(nombre)
            ]
            if faltan:
                raise ValueError(f"DISPARADOR=externo exige {', '.join(faltan)}")
        identidad_url = os.getenv("IDENTIDAD_URL") or os.getenv("KYC_URL", "http://localhost:8105")
        identidad_api_key = os.getenv("DIDIT_API_KEY", "local").strip()
        if os.getenv("IDENTIDAD_URL") and identidad_api_key in ("", "local", VALOR_MARCADOR):
            raise ValueError("IDENTIDAD_URL apunta a un proveedor real y falta DIDIT_API_KEY")
        return cls(
            identidad_url=identidad_url,
            identidad_api_key=identidad_api_key,
            identidad_timeout_s=float(os.getenv("IDENTIDAD_TIMEOUT_SEGUNDOS", "15")),
            identidad_origenes_imagenes=tuple(
                origen.strip()
                for origen in os.getenv(
                    "IDENTIDAD_ORIGENES_IMAGENES",
                    "https://storage.googleapis.com/,http://localhost:4443/,http://fake-gcs:4443/",
                ).split(",")
                if origen.strip()
            ),
            identidad_reescrituras=dict(
                par.split("=", 1)
                for par in os.getenv("IDENTIDAD_REESCRITURA_ORIGENES", "").split(",")
                if "=" in par
            ),
            identidad_llave_cache=_llave_cache_identidad(os.getenv("IDENTIDAD_LLAVE_CACHE", "")),
            identidad_cache_ttl_segundos=int(
                os.getenv("IDENTIDAD_CACHE_TTL_SEGUNDOS", str(30 * 24 * 3600))
            ),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/1"),
            open_finance_url=os.getenv("OPEN_FINANCE_URL", "http://localhost:8101"),
            open_data_url=os.getenv("OPEN_DATA_URL", "http://localhost:8102"),
            pasarela_url=os.getenv("PASARELA_URL", "http://localhost:8103"),
            firma_url=os.getenv("FIRMA_URL", "http://localhost:8104"),
            kyc_url=os.getenv("KYC_URL", "http://localhost:8105"),
            notificaciones_url=os.getenv("NOTIFICACIONES_URL", "http://localhost:8106"),
            adjunto_maximo_bytes=int(os.getenv("ADJUNTO_MAXIMO_BYTES", "5000000")),
            timeout_s=int(timeout_ms_default) / 1000,
            breaker_umbral_fallos=int(os.getenv("BREAKER_UMBRAL_FALLOS", "3")),
            breaker_ttl_segundos=float(os.getenv("BREAKER_TTL_SEGUNDOS", "5")),
            sonda_intervalo_segundos=float(os.getenv("SONDA_INTERVALO_SEGUNDOS", "2")),
            cache_ttl_segundos=int(os.getenv("CACHE_TTL_SEGUNDOS", "300")),
            # db de Redis separado del de la caché (db 1): cola de reconciliación
            # diferida, consumida por `services/consolidador-fuentes` vía `rq`.
            cola_reconciliacion_redis_url=os.getenv(
                "COLA_RECONCILIACION_REDIS_URL", "redis://localhost:6379/2"
            ),
            autenticacion_servicios=os.getenv("AUTENTICACION_SERVICIOS", "ninguna"),
            cache_servir_en_ttl=os.getenv("CACHE_SERVIR_EN_TTL", "false").strip().lower()
            in ("1", "true", "yes", "si"),
            presupuesto_con_cache_ms=int(os.getenv("PRESUPUESTO_CON_CACHE_MS", "300")),
            presupuestos_ms=_presupuestos_desde_entorno(),
            plazo_ruta_sincrona_ms=plazo_ruta_sincrona_ms,
            reserva_saltos_ms=reserva_saltos_ms,
            proveedores_ruta_sincrona=tuple(
                _normalizar_proveedor(nombre)
                for nombre in os.getenv("PROVEEDORES_RUTA_SINCRONA", "firma").split(",")
                if nombre.strip()
            ),
            fuentes_config_path=os.getenv("FUENTES_CONFIG_PATH") or None,
            proveedor_pasarela=proveedor_pasarela,
            proveedor_firma=proveedor_firma,
            disparador=disparador,
            tareas_proyecto=os.getenv("GCP_PROYECTO", ""),
            tareas_region=os.getenv("GCP_REGION", ""),
            tareas_cola_reconciliacion=os.getenv(
                "TAREAS_COLA_RECONCILIACION", "reconciliacion-fuentes"
            ),
            tareas_cuenta=os.getenv("TAREAS_CUENTA", ""),
            consolidador_url=os.getenv("CONSOLIDADOR_URL", ""),
        )
