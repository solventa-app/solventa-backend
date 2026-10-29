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


EstadoSesionPago = Literal["abierta", "procesando", "cobrada", "rechazada", "fallida", "vencida"]
TipoMedioPago = Literal["CREDITO", "DEBITO"]


@dataclass
class SolicitudSesionPago:
    """Sesión de pago en la página alojada de la pasarela (HU-W03, W03-28). El titular captura la
    tarjeta allí; Solventa nunca la ve."""

    id_intento_cobro: str  # idempotente: el mismo intento devuelve la misma sesión
    monto: str  # decimal como texto, nunca float
    moneda: str
    descripcion: str
    url_retorno: str  # adonde vuelve el navegador; la pasarela la valida contra su lista blanca
    idioma: str = "es"


@dataclass
class MedioPagoTokenizado:
    """Lo único que Solventa recibe del medio de pago: el token, la marca y el tipo. Nunca el
    número ni los últimos dígitos (regla #6, W05-A2)."""

    token: str
    marca: str
    tipo: TipoMedioPago


@dataclass
class SesionPago:
    estado: EstadoSesionPago
    sesion_id: str | None = None  # None si la pasarela rechazó la solicitud (p. ej. el retorno)
    url_pago: str | None = None
    vence_en: str | None = None  # ISO 8601
    motivo: str | None = None
    medio: MedioPagoTokenizado | None = None  # solo con `cobrada`
    referencia: str | None = None  # referencia del cobro en la pasarela, solo con `cobrada`


class PuertoPasarelaPagos(Protocol):
    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro: ...

    def crear_sesion_pago(self, solicitud: SolicitudSesionPago) -> SesionPago:
        """Abre (o devuelve, si el intento ya existe) la sesión de la página de pago."""
        ...

    def consultar_sesion_pago(self, sesion_id: str) -> SesionPago | None:
        """Confirmación de servidor a servidor al regresar del navegador. `None` si no existe."""
        ...

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


# `rechazada` = el proveedor no puede entregarlo (p. ej. sin correo): reintentar no lo arregla.
EstadoNotificacion = Literal["enviada", "pendiente", "rechazada"]


@dataclass
class Adjunto:
    """Un archivo que viaja con el aviso (el PDF de la póliza). El contenido va en base64."""

    nombre: str
    tipo: str  # tipo de medio; hoy solo `application/pdf`
    contenido_base64: str


@dataclass
class SolicitudNotificacion:
    """Aviso al cliente de que su póliza fue emitida (HU-W03, CA-W03-08).

    **Regla cambiada en el bloque 8 (decisión W03-16, 2026-10-24).** Antes el proveedor solo recibía
    la plantilla, el identificador opaco del usuario y los datos de la plantilla, y resolvía el
    contacto con su propia base. Ahora AUTH guarda el correo y el teléfono del cliente y el aviso
    lleva el PDF de la póliza, así que el proveedor recibe **datos personales y financieros**: el
    contacto (`correo` o `telefono`) y el adjunto (prima, cobertura, vigencia y nombre del
    asegurado).
    Solo se puede enviar por HTTPS al proveedor y no se guarda en el ACL ni se escribe en logs ni
    en etiquetas de métricas (regla #6). Solo el correo lleva adjuntos: un cliente que solo tenga
    teléfono recibe el aviso sin PDF (W03-17)."""

    id_notificacion: str  # un mismo id recibido dos veces no envía dos avisos (idempotente)
    plantilla: str
    destinatario_id: str  # identificador opaco del usuario
    datos: dict[str, str] = field(default_factory=dict)
    correo: str | None = None
    telefono: str | None = None
    adjuntos: list[Adjunto] = field(default_factory=list)


@dataclass
class ResultadoNotificacion:
    # `pendiente` = no se pudo enviar (proveedor caído o circuito abierto): quien llama reintenta.
    estado: EstadoNotificacion
    referencia: str | None = None
    motivo: str | None = None


class PuertoNotificaciones(Protocol):
    def enviar(self, solicitud: SolicitudNotificacion) -> ResultadoNotificacion: ...


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


# ------------------------------------------------------------ verificación de identidad (HU-M01)

EstadoIdentidad = Literal["aprobada", "rechazada", "pendiente"]


@dataclass(frozen=True)
class ImagenesIdentidad:
    """Las imágenes del onboarding (HU-M01, KAN-31), ya descargadas del almacenamiento. El reverso
    es opcional: un pasaporte no tiene."""

    anverso: bytes
    rostro: bytes
    reverso: bytes | None = None


@dataclass(frozen=True)
class DatosDocumento:
    """Lo que el proveedor extrae del documento. Son datos personales: el ACL los entrega a
    ONBOARDING, que los cifra; nunca se registran (regla #6)."""

    nombres: str | None
    apellidos: str | None
    numero: str | None
    fecha_nacimiento: str | None  # AAAA-MM-DD
    pais: str | None  # ISO 3166-1 alfa-3, como lo entrega el proveedor
    tipo: str | None


@dataclass(frozen=True)
class ResultadoIdentidad:
    """`pendiente` = no se pudo verificar (proveedor caído, circuito abierto o una imagen que no se
    pudo descargar), no que el proveedor esté revisando: ONBOARDING reintenta sin cobrarle al
    cliente un rechazo que no es suyo. `alertas` son códigos del proveedor (p. ej.
    `DOCUMENT_EXPIRED`), sin datos personales."""

    estado: EstadoIdentidad
    motivo: str | None = None
    datos: DatosDocumento | None = None
    documento_autentico: bool | None = None
    coincide_rostro: bool | None = None
    puntaje_rostro: float | None = None
    vivo: bool | None = None
    puntaje_vida: float | None = None
    alertas: tuple[str, ...] = ()


class PuertoVerificacionIdentidad(Protocol):
    def verificar_identidad(
        self, referencia: str, imagenes: ImagenesIdentidad, timeout_s: float | None = None
    ) -> ResultadoIdentidad: ...
