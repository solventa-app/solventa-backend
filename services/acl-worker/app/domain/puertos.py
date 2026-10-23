"""
Puertos de dominio del ACL Worker (HA-MOD-001, arquitectura hexagonal).

El dominio y la capa de aplicación solo conocen estas interfaces: nunca saben si detrás hay un stub,
un sandbox o el proveedor real. Cambiar de proveedor es agregar un adaptador, no tocar el Circuit
Breaker ni los casos de uso (criterio de aceptación de HA-MOD-001 / EC031).

Los puertos son SÍNCRONOS, igual que en el Experimento 1: `purgatory` expone una API síncrona y es
la capa HTTP (FastAPI) quien los ejecuta en un hilo con `asyncio.to_thread`.

PROPUESTA inicial del Sprint 1: afinar las firmas en T-W01-8, T-W01-9 y T-W05-2 y registrar el
cambio en `docs/arquitectura-backend.md`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

# Las 5 fuentes de la pantalla W01-04: 2 de Open Finance y 3 de Open Data.
Fuente = str


@dataclass
class ConsultaFuente:
    cliente_id: str
    consentimiento_id: str  # sin consentimiento vigente no se consulta ninguna fuente (CA-W01-01)
    fuente: Fuente
    finalidad: str = "perfilamiento"  # finalidad autorizada del consentimiento (CE 004/2024)
    forzar_proveedor: bool = False


@dataclass
class DatosFuente:
    fuente: Fuente
    datos: dict[str, Any]
    capturado_en: datetime
    de_cache: bool = False  # True si se degradó al último valor en caché (CA-W01-04, EC010)
    degradado: bool = False  # True si no hay dato real ni caché y se usa el valor por defecto


class PuertoFuenteFinanciera(Protocol):
    """Open Finance (circular 004 de 2024 de la SFC)."""

    def consultar(self, consulta: ConsultaFuente) -> DatosFuente: ...


class PuertoFuenteAbierta(Protocol):
    """Open Data."""

    def consultar(self, consulta: ConsultaFuente) -> DatosFuente: ...


EstadoCobro = Literal["cobrado", "rechazado", "pendiente"]


@dataclass
class OrdenCobro:
    id_intento_cobro: str  # un mismo intento recibido dos veces se ejecuta una vez (CA-W05-04)
    poliza_id: str
    monto: str  # decimal como texto, nunca float
    moneda: str
    token_medio_pago: str  # token del proveedor PCI-DSS: nunca datos de tarjeta (EC031)


@dataclass
class ResultadoCobro:
    estado: EstadoCobro
    referencia: str | None = None
    motivo: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


ResultadoConciliacion = Literal["encontrado", "no_encontrado", "desconocido"]


@dataclass
class ConsultaCobro:
    """Respuesta de la conciliación (KAN-28): ¿la pasarela tiene este cobro?
    `desconocido` = no se le pudo preguntar (caída o circuito abierto)."""

    resultado: ResultadoConciliacion
    cobro: ResultadoCobro | None = None


class PuertoPasarelaPagos(Protocol):
    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro: ...

    def consultar(self, referencia_comercio: str) -> ResultadoCobro | None:
        """Busca un cobro por la **referencia del comercio**, que es la
        `id_intento_cobro` que Solventa envía al cobrar. Así consultan las pasarelas
        reales (p. ej. Wompi `reference`, PayU `referenceCode`), de modo que cambiar de
        proveedor sigue siendo solo agregar un adaptador (CA-W05-06). `None` si no existe."""
        ...


EstadoFirma = Literal["firmada", "pendiente"]


@dataclass
class SolicitudFirma:
    """Firma electrónica del documento de la póliza (HU-W03, CA-W03-03, no repudio).

    El proveedor recibe solo el **hash** del documento, nunca su contenido: el contenido (con la
    prima) vive cifrado en POLICY junto a la evidencia, y con el hash se puede probar que es el que
    se firmó."""

    id_firma: str  # un mismo id recibido dos veces devuelve la misma evidencia (idempotente)
    documento_hash: str  # SHA-256 en hexadecimal
    version_documento: str
    firmante_id: str  # identificador opaco del usuario que firma, nunca un documento ni un correo


@dataclass
class ResultadoFirma:
    estado: EstadoFirma
    referencia: str | None = None  # la evidencia que entrega el proveedor
    firmada_en: str | None = None  # sello de tiempo del proveedor (ISO 8601)
    motivo: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


class PuertoFirmaElectronica(Protocol):
    def firmar(self, solicitud: SolicitudFirma) -> ResultadoFirma: ...


EstadoKyc = Literal["aprobada", "rechazada", "pendiente"]


@dataclass
class SolicitudKyc:
    """Verificación de identidad del cliente (KYC) para decidir la suscripción (HU-W03).

    Solo viaja el identificador opaco del cliente (el HMAC que calcula el BFF), nunca un documento
    ni un dato personal. Un proveedor real pediría más (documento, validación biométrica): ese dato
    llegaría por un canal propio, no por este puerto (ver `docs/plan-completar-hu-w03.md`, W03-10).
    """

    id_verificacion: str  # un mismo id recibido dos veces devuelve el mismo resultado (idempotente)
    cliente_id: str


@dataclass
class ResultadoKyc:
    # `pendiente` = no se pudo verificar (proveedor caído o circuito abierto), no que el proveedor
    # esté revisando: quien decide la suscripción lo manda a revisión manual.
    estado: EstadoKyc
    referencia: str | None = None
    motivo: str | None = None
    verificada_en: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


class PuertoKyc(Protocol):
    def verificar(self, solicitud: SolicitudKyc) -> ResultadoKyc: ...


@dataclass
class TablaTasas:
    """Cuántos COP vale una unidad de cada moneda (KAN-209). Valores como texto decimal: nunca
    float. `fecha` y `fuente` dicen de cuándo y de dónde es la tabla."""

    cop_por_unidad: dict[str, str]
    fecha: str
    fuente: str


class PuertoTasasCambio(Protocol):
    """Tasas de cambio para mostrar montos en la moneda que el usuario elige en la web."""

    def tasas(self) -> TablaTasas: ...
