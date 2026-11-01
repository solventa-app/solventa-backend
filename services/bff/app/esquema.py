"""Esquema GraphQL del BFF: espejo 1:1 de `contracts/graphql/schema.graphql`.

El contrato manda (regla #8). `tests/test_esquema_contrato.py` compara este
esquema con el contrato en ambos sentidos: si difieren, se corrige el código,
no el contrato.

Operaciones sin backend todavía (alcance del Sprint 1, KAN-24):
- `contactarAsesor` es un MOCK que solo confirma.
- HU-W03 (KAN-26): `aceptarOferta`, `propuesta`, `documentoFirma`, `firmarPropuesta` y `poliza` ya
  leen y escriben en POLICY (bloques 1, 2 y 4). `Poliza.documentoUrl` apunta a una ruta del BFF que
  entrega el PDF y que llega con el bloque 5. `oferta(id)` lee la oferta que RATING guardó, con la
  misma validación de dueño que `aceptarOferta`.
- HU-W03 bloque 9 (contrato v0.18, F-B6): las operaciones del asesor, las de la página del enlace
  (token del enlace o sesión de CLIENTE, W03-33), `propuestas` (filtradas por el rol del token) y
  `ticketAvisos` leen y escriben en POLICY y AUTH a través de `ServicioFirmaPago`.
`cobros` y `reintentarCobro` (KAN-28) leen y reintentan en PAYMENTS, que concilia
con la pasarela antes de cobrar (ver `docs/cobros-conciliacion.md`).
Ninguna produce un 5xx: los errores de GraphQL viajan con HTTP 200.

Permisos (HU-M02): cada resolutor llama a `exigir_rol` con los roles que admite. La
matriz completa está en `docs/autenticacion-usuarios.md`."""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Annotated

import strawberry
from graphql import GraphQLError
from strawberry.extensions import MaskErrors
from strawberry.types import Info

from app.autenticacion_usuarios import (
    MENSAJE_SIN_PERMISO,
    MENSAJE_SIN_SESION,
    MENSAJE_VERIFICACION_PENDIENTE,
    UsuarioActual,
)
from app.clientes import (
    ArchivoRechazado,
    ErrorOnboardingServicio,
    ErrorServicio,
    ResolucionRechazada,
    SolicitudInvalida,
    VerificacionPendiente,
)
from app.identificadores import cliente_id_desde_documento, id_opaco
from app.idioma import TraducirMensajes
from app.jurisdicciones import JURISDICCIONES
from app.monedas import (
    MAXIMO_MONTOS,
    MENSAJE_DEMASIADOS_MONTOS,
    MENSAJE_TASAS_NO_DISPONIBLES,
    ConversionInvalida,
    convertir,
)
from app.reglas_clave import motivo_clave_invalida
from app.revocacion import avisar_revocacion
from app.servicio_firma_pago import (
    MENSAJE_AVISOS_NO_DISPONIBLES,
    MENSAJE_POLIZA_YA_EMITIDA,
    MENSAJE_PROPUESTAS_NO_DISPONIBLES,
    ErrorFlujoDatos,
    ServicioFirmaPago,
    documento_del_cliente,
)
from app.servicio_oferta import ErrorOfertaDatos
from app.servicio_suscripcion import (
    MENSAJE_SUSCRIPCION_NO_DISPONIBLE,
    ErrorAceptacionDatos,
    ErrorFirmaDatos,
    ServicioSuscripcion,
    explicar_estado,
)
from app.trazas_graphql import TrazaGraphQL

logger = logging.getLogger("bff.cobros")
# POLICY no tiene producto: hay uno solo y su ramo lo pone el BFF (W04-4, HU-W04).
RAMO_POLIZA = os.getenv("RAMO_POLIZA", "Vida grupo deudor con amparo de daños al bien financiado")
TAMANO_LOTE_NOMBRES = 200  # el máximo de ids por llamada que admite AUTH


class ErrorPublico(Exception):
    """Error cuyo mensaje SÍ puede ver el cliente. Cualquier otra excepción se
    enmascara como "Unexpected error." (sin detalles internos)."""


def _mostrar_solo_errores_publicos(error: GraphQLError) -> bool:
    """True = enmascarar. Solo se oculta lo que salió de una excepción inesperada
    dentro de un resolutor. Los errores de validación del esquema (campo
    inexistente, tipo equivocado) no tienen `original_error`, no revelan nada
    interno y el frontend los necesita para depurar."""
    return error.original_error is not None and not isinstance(error.original_error, ErrorPublico)


def exigir_rol(
    info: Info, *roles: str, siempre: bool = False, admite_pendiente: bool = False
) -> UsuarioActual | None:
    """Aplica la autorización de una operación y devuelve el usuario (o `None`).

    - Token presente pero inválido o vencido: error, en cualquier modo.
    - Sin token: error si `siempre` o si la bandera está en `obligatoria`; si no
      (modo `opcional`), se permite como antes y devuelve `None`.
    - Con token: el rol debe estar en `roles`.
    - Un token con el scope `verificacion-pendiente` (HU-M01) solo entra a las operaciones con
      `admite_pendiente` (`yo`, `miVerificacion`, `actualizarSesion`)."""
    contexto = info.context
    if contexto["error_token"]:
        raise ErrorPublico(contexto["error_token"])
    usuario = contexto["usuario"]
    if usuario is None:
        if siempre or contexto["modo_autenticacion"] == "obligatoria":
            raise ErrorPublico(MENSAJE_SIN_SESION)
        return None
    if usuario.rol not in roles:
        raise ErrorPublico(MENSAJE_SIN_PERMISO)
    if usuario.verificacion_pendiente and not admite_pendiente:
        raise ErrorPublico(MENSAJE_VERIFICACION_PENDIENTE)
    return usuario


# ---------- Tipos ----------


@strawberry.enum
class EstadoConsentimiento(Enum):
    VIGENTE = "VIGENTE"
    REVOCADO = "REVOCADO"
    VENCIDO = "VENCIDO"


@strawberry.enum
class TipoOferta(Enum):
    COMPLETA = "COMPLETA"
    PRELIMINAR = "PRELIMINAR"


@strawberry.enum
class EstadoFuente(Enum):
    OK = "OK"
    CACHE = "CACHE"
    FALLO = "FALLO"


@strawberry.enum
class NivelRiesgo(Enum):
    BAJO = "BAJO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"


@strawberry.type
class PerfilRiesgo:
    score: int
    nivel_riesgo: NivelRiesgo
    fecha_vigencia: datetime


@strawberry.enum
class MotivoErrorOferta(Enum):
    CONSENTIMIENTO_NO_VIGENTE = "CONSENTIMIENTO_NO_VIGENTE"
    DATOS_INSUFICIENTES = "DATOS_INSUFICIENTES"
    FUENTES_NO_DISPONIBLES = "FUENTES_NO_DISPONIBLES"


@strawberry.enum
class OrigenFuente(Enum):
    OPEN_FINANCE = "OPEN_FINANCE"
    OPEN_DATA = "OPEN_DATA"


@strawberry.enum
class CanalContacto(Enum):
    LLAMAR = "LLAMAR"
    CHAT = "CHAT"
    AGENDAR = "AGENDAR"


@strawberry.enum
class EstadoCobro(Enum):
    COBRANDO = "COBRANDO"
    COBRADO = "COBRADO"
    RECHAZADO = "RECHAZADO"
    PENDIENTE = "PENDIENTE"


@strawberry.type
class Consentimiento:
    id: strawberry.ID
    finalidad: str
    version_texto: str
    otorgado_en: datetime
    revocado_en: datetime | None
    vence_en: datetime
    vigente: bool
    estado: EstadoConsentimiento


@strawberry.input
class ConsentimientoInput:
    finalidad: str
    version_texto: str
    documento_identidad: str | None = None
    tipo_dato: str | None = None
    fuente_datos: str | None = None
    canal: str | None = None


@strawberry.type
class Jurisdiccion:
    """v0.25 (T18): límites por país, de jurisdicciones.json."""

    codigo: str
    nombre: str
    moneda: str
    tipo_documento: str
    plazo_min: int
    plazo_max: int


@strawberry.type
class Monto:
    valor: str  # decimal como texto, nunca float
    moneda: str


@strawberry.type
class RangoPrima:
    minima: Monto
    maxima: Monto


@strawberry.type
class FactorScore:
    nombre: str
    peso: float


@strawberry.type
class FuenteConsultada:
    id: strawberry.ID
    nombre: str
    origen: OrigenFuente
    estado: EstadoFuente
    capturado_en: datetime | None


@strawberry.type
class Oferta:
    id: strawberry.ID
    tipo: TipoOferta
    prima: Monto | None
    rango_prima: RangoPrima | None
    cobertura: Monto
    desglose_score: list[FactorScore]
    fuentes: list[FuenteConsultada]
    perfil_riesgo: PerfilRiesgo | None = None


@strawberry.type
class ErrorOferta:
    mensaje: str
    puede_reintentar: bool
    motivo: MotivoErrorOferta | None = None


ResultadoOferta = Annotated[Oferta | ErrorOferta, strawberry.union("ResultadoOferta")]


@strawberry.input
class SolicitudOfertaInput:
    consentimiento_id: strawberry.ID
    valor_credito: str
    plazo_meses: int
    direccion_inmueble: str
    # Obligatorio para un ASESOR; un CLIENTE con documento en AUTH no lo manda (v0.18, W03-34).
    documento_identidad: str | None = None


@strawberry.type
class ConfirmacionContacto:
    canal: CanalContacto
    resumen: str


@strawberry.enum
class OrigenCobro(Enum):
    INTERACTIVO = "INTERACTIVO"
    RECURRENTE = "RECURRENTE"


@strawberry.enum
class TipoMedioPago(Enum):
    CREDITO = "CREDITO"
    DEBITO = "DEBITO"


@strawberry.type
class MedioPago:
    marca: str
    tipo: TipoMedioPago


@strawberry.type
class ClienteCobro:
    id: strawberry.ID
    nombre: str | None  # dato personal; null si AUTH no respondió o no tiene nombre


@strawberry.type
class Cobro:
    id: strawberry.ID
    poliza_id: strawberry.ID
    estado: EstadoCobro
    monto: Monto
    actualizado_en: datetime
    motivo: str | None  # por qué quedó pendiente o rechazado
    intentos: int | None
    reintentos_restantes: int | None  # reintentos manuales de un rechazado (CA-W05-02)
    id_transaccion: str | None  # referencia de la pasarela (columna de W05-01)
    cliente: ClienteCobro | None
    origen: OrigenCobro | None = None  # v0.18 (W05-A3)
    medio: MedioPago | None = None  # v0.18 (W05-A5): marca y tipo, nunca el número


@strawberry.input
class MontoInput:
    valor: str  # decimal como texto, nunca float
    moneda: str


@strawberry.input
class CrearCobroInput:
    # Lo genera el cliente (un UUID por intento) y lo reenvía igual si repite la orden.
    id_intento_cobro: strawberry.ID
    poliza_id: strawberry.ID
    monto: MontoInput
    token_medio_pago: str  # token del proveedor PCI-DSS; nunca datos de tarjeta
    # El CLIENTE a quien se le cobra. Mientras no exista el servicio de pólizas, nada valida
    # que la póliza sea suya.
    cliente_id: strawberry.ID | None = None


@strawberry.type
class ErrorCobro:
    mensaje: str


ResultadoCobro = Annotated[Cobro | ErrorCobro, strawberry.union("ResultadoCobro")]


# ---------- Suscripción, firma y póliza (HU-W03) ----------


@strawberry.enum
class EstadoPropuesta(Enum):
    ACEPTADA = "ACEPTADA"
    EN_EMISION = "EN_EMISION"
    EN_REVISION = "EN_REVISION"
    PENDIENTE_PAGO = "PENDIENTE_PAGO"
    PAGO_RECHAZADO = "PAGO_RECHAZADO"
    NO_EMITIDA = "NO_EMITIDA"
    EMITIDA = "EMITIDA"
    # v0.18 (HU-W03, bloque 9)
    IDENTIDAD_VERIFICADA = "IDENTIDAD_VERIFICADA"
    EN_FIRMA = "EN_FIRMA"
    FIRMADA = "FIRMADA"
    CANCELADA = "CANCELADA"


@strawberry.enum
class EstadoVisible(Enum):
    """Los tres estados de la tabla «Mis pólizas» / «Pólizas emitidas» (W03-35)."""

    EMITIENDO = "EMITIENDO"
    EMITIDA = "EMITIDA"
    NO_EMITIDA = "NO_EMITIDA"


def estado_visible(estado: str) -> EstadoVisible:
    if estado == "EMITIDA":
        return EstadoVisible.EMITIDA
    if estado in ("NO_EMITIDA", "CANCELADA"):
        return EstadoVisible.NO_EMITIDA
    return EstadoVisible.EMITIENDO


@strawberry.enum
class MotivoErrorAceptacion(Enum):
    OFERTA_PRELIMINAR = "OFERTA_PRELIMINAR"
    OFERTA_VENCIDA = "OFERTA_VENCIDA"
    OFERTA_NO_ENCONTRADA = "OFERTA_NO_ENCONTRADA"
    CONSENTIMIENTO_NO_VIGENTE = "CONSENTIMIENTO_NO_VIGENTE"


@strawberry.enum
class MotivoErrorFirma(Enum):
    COBRO_NO_AUTORIZADO = "COBRO_NO_AUTORIZADO"
    VERSION_DOCUMENTO_DESACTUALIZADA = "VERSION_DOCUMENTO_DESACTUALIZADA"
    PROPUESTA_NO_FIRMABLE = "PROPUESTA_NO_FIRMABLE"
    SERVICIO_FIRMA_NO_DISPONIBLE = "SERVICIO_FIRMA_NO_DISPONIBLE"
    ENLACE_VENCIDO = "ENLACE_VENCIDO"
    IMAGEN_INVALIDA = "IMAGEN_INVALIDA"


@strawberry.enum
class MotivoEstadoPropuesta(Enum):
    PAGO_FONDOS_INSUFICIENTES = "PAGO_FONDOS_INSUFICIENTES"
    PAGO_RECHAZADO = "PAGO_RECHAZADO"
    PAGO_EN_PROCESO = "PAGO_EN_PROCESO"
    REVISION_MANUAL = "REVISION_MANUAL"
    NO_APROBADA = "NO_APROBADA"


@strawberry.enum
class AccionSugerida(Enum):
    ESPERAR = "ESPERAR"
    CONTACTAR_ASESOR = "CONTACTAR_ASESOR"


@strawberry.enum
class EstadoPoliza(Enum):
    EMITIDA = "EMITIDA"


@strawberry.type
class Poliza:
    id: strawberry.ID
    numero: str
    estado: EstadoPoliza
    vigencia_desde: datetime
    vigencia_hasta: datetime
    prima: Monto
    cobertura: Monto
    documento_url: str  # ruta del BFF que entrega el PDF
    ramo: str  # v0.21 (HU-W04): un solo producto hoy, por configuración (RAMO_POLIZA)


@strawberry.type
class Propuesta:
    id: strawberry.ID
    oferta_id: strawberry.ID
    estado: EstadoPropuesta
    prima: Monto
    cobertura: Monto
    plazo_meses: int
    actualizado_en: datetime
    poliza_id: strawberry.ID | None = None  # presente cuando el estado es EMITIDA
    # Presentes en EN_REVISION, PENDIENTE_PAGO, PAGO_RECHAZADO y NO_EMITIDA (CA-W03-06).
    motivo_estado: MotivoEstadoPropuesta | None = None
    mensaje_estado: str | None = None
    accion_sugerida: AccionSugerida | None = None
    # v0.18 (W03-35)
    estado_visible: EstadoVisible | None = None
    creada_en: datetime | None = None
    poliza: Poliza | None = None  # presente en EMITIDA
    # Datos personales: solo para el ASESOR que la aceptó y el BACKOFFICE.
    nombre_titular: str | None = None
    nombre_asesor: str | None = None  # solo para el BACKOFFICE
    nombre_socio: str | None = None  # v0.22 (HU-W07): solo para el BACKOFFICE


@strawberry.type
class PaginaPropuestas:
    propuestas: list[Propuesta]
    total: int
    pagina: int
    tamano: int


@strawberry.type
class ErrorAceptacion:
    mensaje: str
    puede_reintentar: bool
    motivo: MotivoErrorAceptacion | None = None


ResultadoAceptacion = Annotated[
    Propuesta | ErrorAceptacion, strawberry.union("ResultadoAceptacion")
]


@strawberry.type
class DocumentoFirma:
    propuesta_id: strawberry.ID
    version: str  # se reenvía en `FirmarPropuestaInput.versionDocumento`
    resumen: str
    monto_mensual: Monto
    meses: int
    forma_cobro: str


@strawberry.input
class FirmarPropuestaInput:
    propuesta_id: strawberry.ID
    autoriza_cobro: bool  # debe ser true: la firma autoriza el cobro recurrente
    version_documento: str


@strawberry.type
class FirmaRegistrada:
    propuesta_id: strawberry.ID
    firmada_en: datetime
    referencia: str  # referencia de la evidencia de no repudio
    estado: EstadoPropuesta


@strawberry.type
class ErrorFirma:
    mensaje: str
    puede_reintentar: bool
    motivo: MotivoErrorFirma | None = None


ResultadoFirma = Annotated[FirmaRegistrada | ErrorFirma, strawberry.union("ResultadoFirma")]


# ---------- Firma y pago con el asesor o en la sesión del CLIENTE (v0.18, bloque 9) ----------


@strawberry.input
class ContactoTitularInput:
    nombre: str  # 2 a 100 caracteres
    correo: str  # se guarda cifrado y en minúsculas


@strawberry.enum
class MotivoErrorCodigo(Enum):
    CODIGO_INVALIDO = "CODIGO_INVALIDO"
    CODIGO_VENCIDO = "CODIGO_VENCIDO"
    INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"
    PROPUESTA_NO_VALIDA = "PROPUESTA_NO_VALIDA"


@strawberry.type
class CodigoEnviado:
    propuesta_id: strawberry.ID
    correo_enmascarado: str  # nunca el correo completo
    vence_en: datetime


@strawberry.type
class ErrorCodigo:
    mensaje: str
    puede_reintentar: bool
    motivo: MotivoErrorCodigo | None = None
    intentos_restantes: int | None = None


ResultadoEnvioCodigo = Annotated[
    CodigoEnviado | ErrorCodigo, strawberry.union("ResultadoEnvioCodigo")
]
ResultadoVerificacion = Annotated[
    Propuesta | ErrorCodigo, strawberry.union("ResultadoVerificacion")
]


@strawberry.type
class EnlaceEnviado:
    propuesta: Propuesta
    correo_enmascarado: str
    vence_en: datetime  # la URL nunca vuelve al asesor


@strawberry.type
class ErrorEmision:
    mensaje: str
    puede_reintentar: bool


ResultadoEmision = Annotated[
    EnlaceEnviado | Propuesta | ErrorEmision, strawberry.union("ResultadoEmision")
]


@strawberry.enum
class ModoFirma(Enum):
    TRAZO = "TRAZO"
    IMAGEN = "IMAGEN"
    NOMBRE = "NOMBRE"


@strawberry.type
class EnlaceFirma:
    vigente: bool
    propuesta: Propuesta
    vence_en: datetime | None = None  # null con sesión de CLIENTE
    documento: DocumentoFirma | None = None
    documento_url: str | None = None
    firma: FirmaRegistrada | None = None
    poliza: Poliza | None = None


@strawberry.input
class FirmarPorEnlaceInput:
    version_documento: str
    acepta_terminos: bool
    autoriza_cobro: bool
    modo: ModoFirma
    propuesta_id: strawberry.ID | None = None  # obligatorio solo con sesión de CLIENTE
    imagen_archivo_id: strawberry.ID | None = None  # TRAZO e IMAGEN
    nombre_firma: str | None = None  # NOMBRE


@strawberry.type
class SesionPago:
    sesion_id: strawberry.ID
    url_pago: str  # página de la pasarela, fuera de Solventa
    vence_en: datetime


@strawberry.enum
class MotivoErrorPago(Enum):
    PAGO_RECHAZADO = "PAGO_RECHAZADO"
    FONDOS_INSUFICIENTES = "FONDOS_INSUFICIENTES"
    TARJETA_VENCIDA = "TARJETA_VENCIDA"
    PASARELA_NO_DISPONIBLE = "PASARELA_NO_DISPONIBLE"
    REQUIERE_OTRA_TARJETA = "REQUIERE_OTRA_TARJETA"
    SESION_VENCIDA = "SESION_VENCIDA"
    ENLACE_VENCIDO = "ENLACE_VENCIDO"


@strawberry.type
class ErrorPago:
    mensaje: str
    puede_reintentar: bool
    motivo: MotivoErrorPago | None = None
    fallos_seguidos: int | None = None


ResultadoInicioPago = Annotated[SesionPago | ErrorPago, strawberry.union("ResultadoInicioPago")]
ResultadoPago = Annotated[Poliza | ErrorPago, strawberry.union("ResultadoPago")]


@strawberry.type
class TicketAvisos:
    """Ticket de un solo uso (60 s) para abrir el stream SSE de `avisos` (W03-37)."""

    ticket: str
    url: str
    vence_en: datetime


# ---------- Usuarios y sesión (HU-M02) ----------


@strawberry.enum
class RolUsuario(Enum):
    CLIENTE = "CLIENTE"
    ASESOR = "ASESOR"
    BACKOFFICE = "BACKOFFICE"


@strawberry.enum
class EstadoUsuario(Enum):
    ACTIVO = "ACTIVO"
    SUSPENDIDO = "SUSPENDIDO"
    # v0.20 (HU-M01): cuentas del onboarding del móvil.
    PENDIENTE_VERIFICACION = "PENDIENTE_VERIFICACION"
    RECHAZADA = "RECHAZADA"


@strawberry.type
class Usuario:
    id: strawberry.ID
    usuario: str  # alias de acceso: nunca un correo ni un documento
    rol: RolUsuario
    estado: EstadoUsuario
    creado_en: datetime
    # Dato personal: solo `iniciarSesion` y `yo` lo traen (cada quien, el suyo); AUTH lo
    # deja en null en listas y altas. Nunca viaja dentro del JWT.
    nombre: str | None = None
    # Preferencias de la interfaz (KAN-209): la web las aplica al iniciar sesión.
    idioma: str = "ES"
    moneda: str = "COP"
    # Solo dice si ya dejó un contacto (correo o teléfono) para el aviso de su póliza (HU-W03,
    # bloque 8); nunca cuál es: el contacto se escribe y no vuelve por GraphQL.
    tiene_contacto: bool = False
    # v0.18 (W03-34): solo en `iniciarSesion` y `yo`; nunca el documento completo.
    documento_enmascarado: str | None = None
    # v0.24: el correo del propio usuario (perfil común); solo en `iniciarSesion` y `yo`.
    correo: str | None = None


@strawberry.type
class Sesion:
    token: str
    expira_en: datetime
    usuario: Usuario


@strawberry.type
class ErrorAutenticacion:
    mensaje: str
    # v0.20 (HU-M01): VERIFICACION_PENDIENTE fuera de la app; null en credenciales incorrectas.
    codigo: str | None = None


ResultadoSesion = Annotated[Sesion | ErrorAutenticacion, strawberry.union("ResultadoSesion")]


@strawberry.type
class ErrorUsuario:
    mensaje: str


ResultadoUsuario = Annotated[Usuario | ErrorUsuario, strawberry.union("ResultadoUsuario")]


@strawberry.input
class CredencialesInput:
    usuario: str
    clave: str
    # T17: rol del chip elegido en la web. Si no es el de la cuenta, la respuesta es la misma que
    # con una clave errada (no se revela qué rol tiene el usuario).
    rol: RolUsuario | None = None


@strawberry.input
class CrearUsuarioInput:
    usuario: str
    clave: str
    rol: RolUsuario
    nombre: str | None = None  # solo CLIENTE; AUTH lo guarda cifrado y no lo devuelve
    # Solo CLIENTE (HU-W03, bloque 8); AUTH los guarda cifrados y no los devuelve.
    correo: str | None = None
    telefono: str | None = None
    # v0.18 (W03-27): obligatorio para un CLIENTE, junto con el correo (lo exige AUTH).
    documento_identidad: str | None = None


@strawberry.input
class PreferenciasInput:
    idioma: str  # ES | EN
    moneda: str  # COP | USD | EUR | MXN | BRL | CLP | PEN


@strawberry.input
class ContactoInput:
    """Al menos uno de los dos; lo que no llega se conserva."""

    correo: str | None = None
    telefono: str | None = None


# ---------- Traducción desde los servicios ----------


def _fecha(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


def _consentimiento(datos: dict) -> Consentimiento:
    otorgado_en = datetime.fromisoformat(datos["otorgado_en"])
    revocado_en = _fecha(datos.get("revocado_en"))
    vence_en = (
        datetime.fromisoformat(datos["vence_en"])
        if datos.get("vence_en")
        else otorgado_en + timedelta(days=30)
    )
    vigente = bool(datos["vigente"])
    if datos.get("estado"):
        estado = EstadoConsentimiento(datos["estado"])
    elif revocado_en is not None:
        estado = EstadoConsentimiento.REVOCADO
    elif not vigente:
        estado = EstadoConsentimiento.VENCIDO
    else:
        estado = EstadoConsentimiento.VIGENTE
    return Consentimiento(
        id=strawberry.ID(datos["id"]),
        finalidad=datos["finalidad"],
        version_texto=datos["version_texto"],
        otorgado_en=otorgado_en,
        revocado_en=revocado_en,
        vence_en=vence_en,
        vigente=vigente,
        estado=estado,
    )


def _monto(datos: dict | None) -> Monto | None:
    return Monto(valor=datos["valor"], moneda=datos["moneda"]) if datos else None


def _propuesta(
    datos: dict,
    poliza: Poliza | None = None,
    nombre_titular: str | None = None,
    nombre_asesor: str | None = None,
    nombre_socio: str | None = None,
) -> Propuesta:
    explicacion = explicar_estado(datos)
    return Propuesta(
        id=datos["id"],
        oferta_id=datos["oferta_id"],
        estado=EstadoPropuesta(datos["estado"]),
        prima=Monto(valor=datos["prima"]["valor"], moneda=datos["prima"]["moneda"]),
        cobertura=Monto(valor=datos["cobertura"]["valor"], moneda=datos["cobertura"]["moneda"]),
        plazo_meses=datos.get("plazo_meses") or 0,
        actualizado_en=_fecha(datos["actualizado_en"]),
        poliza_id=datos.get("poliza_id"),
        motivo_estado=MotivoEstadoPropuesta(explicacion.motivo) if explicacion else None,
        mensaje_estado=explicacion.mensaje if explicacion else None,
        accion_sugerida=AccionSugerida(explicacion.accion) if explicacion else None,
        estado_visible=estado_visible(datos["estado"]),
        creada_en=_fecha(datos.get("creado_en")),
        poliza=poliza,
        nombre_titular=nombre_titular,
        nombre_asesor=nombre_asesor,
        nombre_socio=nombre_socio,
    )


def _poliza_de_resumen(resumen: dict | None, propuesta: dict) -> Poliza | None:
    """La lista de POLICY trae de la póliza solo número y vigencia; el precio es el de la
    propuesta."""
    if not resumen:
        return None
    return _poliza(
        {
            **resumen,
            "estado": "EMITIDA",
            "prima": propuesta["prima"],
            "cobertura": propuesta["cobertura"],
        }
    )


def _poliza(datos: dict) -> Poliza:
    return Poliza(
        id=datos["id"],
        numero=datos["numero"],
        estado=EstadoPoliza(datos["estado"]),
        vigencia_desde=_fecha(datos["vigencia_desde"]),
        vigencia_hasta=_fecha(datos["vigencia_hasta"]),
        prima=Monto(valor=datos["prima"]["valor"], moneda=datos["prima"]["moneda"]),
        cobertura=Monto(valor=datos["cobertura"]["valor"], moneda=datos["cobertura"]["moneda"]),
        # El PDF lo entrega el BFF en esta ruta (bloque 5); hasta entonces responde 404.
        documento_url=f"/polizas/{datos['id']}/documento",
        ramo=RAMO_POLIZA,
    )


def _documento_firma(datos: dict) -> DocumentoFirma:
    return DocumentoFirma(
        propuesta_id=datos["propuesta_id"],
        version=datos["version"],
        resumen=datos["resumen"],
        monto_mensual=Monto(
            valor=datos["monto_mensual"]["valor"], moneda=datos["monto_mensual"]["moneda"]
        ),
        meses=datos["meses"],
        forma_cobro=datos["forma_cobro"],
    )


def _perfil_riesgo(datos: dict | None) -> PerfilRiesgo | None:
    if not datos:
        return None
    return PerfilRiesgo(
        score=int(datos["score"]),
        nivel_riesgo=NivelRiesgo(datos["nivelRiesgo"]),
        fecha_vigencia=datetime.fromisoformat(datos["fechaVigencia"]),
    )


def _oferta(datos: dict) -> Oferta:
    """`datos` es la respuesta de RATING, ya en el camelCase del contrato."""
    rango = datos.get("rangoPrima")
    return Oferta(
        id=strawberry.ID(datos["id"]),
        tipo=TipoOferta(datos["tipo"]),
        prima=_monto(datos.get("prima")),
        rango_prima=(
            RangoPrima(minima=_monto(rango["minima"]), maxima=_monto(rango["maxima"]))
            if rango
            else None
        ),
        cobertura=_monto(datos["cobertura"]),
        desglose_score=[
            FactorScore(nombre=f["nombre"], peso=f["peso"]) for f in datos["desgloseScore"]
        ],
        fuentes=[
            FuenteConsultada(
                id=strawberry.ID(f["id"]),
                nombre=f["nombre"],
                origen=OrigenFuente(f["origen"]),
                estado=EstadoFuente(f["estado"]),
                capturado_en=_fecha(f.get("capturadoEn")),
            )
            for f in datos["fuentes"]
        ],
        perfil_riesgo=_perfil_riesgo(datos.get("perfilRiesgo")),
    )


def _cobro(datos: dict, nombres: dict[str, str] | None = None) -> Cobro:
    """`datos` es la respuesta de PAYMENTS (estados en minúscula, `monto` y `moneda` sueltos).
    `nombres` trae el de AUTH por `cliente_id` y, si el titular no tiene cuenta, el de POLICY por la
    propuesta (`poliza_id`, ver W03-B1); sin él, el cliente sale sin nombre."""
    cliente_id = datos.get("cliente_id")
    nombres = nombres or {}
    return Cobro(
        id=strawberry.ID(datos["id"]),
        poliza_id=strawberry.ID(datos["poliza_id"]),
        estado=EstadoCobro(datos["estado"].upper()),
        monto=Monto(valor=datos["monto"], moneda=datos["moneda"]),
        actualizado_en=datetime.fromisoformat(datos["actualizado_en"]),
        motivo=datos.get("motivo"),
        intentos=datos.get("intentos"),
        reintentos_restantes=datos.get("reintentos_rechazo_restantes"),
        id_transaccion=datos.get("referencia"),
        cliente=(
            ClienteCobro(
                id=strawberry.ID(cliente_id),
                nombre=nombres.get(cliente_id) or nombres.get(datos["poliza_id"]),
            )
            if cliente_id
            else None
        ),
        origen=OrigenCobro(datos["origen"].upper()) if datos.get("origen") else None,
        medio=(
            MedioPago(marca=datos["medio"]["marca"], tipo=TipoMedioPago(datos["medio"]["tipo"]))
            if datos.get("medio")
            else None
        ),
    )


def _usuario(datos: dict) -> Usuario:
    return Usuario(
        id=strawberry.ID(datos["id"]),
        usuario=datos["usuario"],
        rol=RolUsuario(datos["rol"]),
        estado=EstadoUsuario(datos["estado"]),
        creado_en=datetime.fromisoformat(datos["creado_en"]),
        nombre=datos.get("nombre"),
        idioma=datos.get("idioma", "ES"),
        moneda=datos.get("moneda", "COP"),
        tiene_contacto=bool(datos.get("tiene_contacto", False)),
        documento_enmascarado=datos.get("documento_enmascarado"),
        correo=datos.get("correo"),
    )


# ---------- Archivos (KAN-250) ----------


@strawberry.enum
class EstadoArchivo(Enum):
    PENDIENTE = "PENDIENTE"
    DISPONIBLE = "DISPONIBLE"
    RECHAZADO = "RECHAZADO"
    ELIMINADO = "ELIMINADO"


@strawberry.type
class Archivo:
    id: strawberry.ID
    tipo: str
    estado: EstadoArchivo
    tipo_contenido: str
    tamano_bytes: int | None
    motivo: str | None
    creado_en: datetime


@strawberry.type
class EncabezadoSubida:
    nombre: str
    valor: str


@strawberry.type
class SubidaArchivo:
    archivo: Archivo
    url: str
    metodo: str
    encabezados: list[EncabezadoSubida]
    vence_en: datetime


@strawberry.type
class DescargaArchivo:
    url: str
    vence_en: datetime


@strawberry.type
class ErrorArchivo:
    codigo: str
    mensaje: str


ResultadoSubidaArchivo = Annotated[
    SubidaArchivo | ErrorArchivo, strawberry.union("ResultadoSubidaArchivo")
]
ResultadoArchivo = Annotated[Archivo | ErrorArchivo, strawberry.union("ResultadoArchivo")]
ResultadoDescargaArchivo = Annotated[
    DescargaArchivo | ErrorArchivo, strawberry.union("ResultadoDescargaArchivo")
]


@strawberry.input
class SolicitudSubidaArchivoInput:
    tipo: str
    tipo_contenido: str
    tamano_bytes: int
    propietario_id: strawberry.ID | None = None
    consentimiento_id: strawberry.ID | None = None


def _archivo(datos: dict) -> Archivo:
    return Archivo(
        id=strawberry.ID(datos["id"]),
        tipo=datos["tipo"],
        estado=EstadoArchivo(datos["estado"]),
        tipo_contenido=datos["tipo_contenido"],
        tamano_bytes=datos.get("tamano_bytes"),
        motivo=datos.get("motivo"),
        creado_en=datetime.fromisoformat(datos["creado_en"]),
    )


# Mensaje para mostrar por código de ALMACENAMIENTO; el código viaja tal cual en `codigo`.
MENSAJES_ARCHIVO = {
    "TIPO_NO_ADMITIDO": "Este tipo de archivo no se admite.",
    "TIPO_CONTENIDO_NO_ADMITIDO": "El formato del archivo no se admite.",
    "TAMANO_NO_ADMITIDO": "El archivo supera el tamaño permitido.",
    "NO_AUTORIZADO": "No tienes permiso para este archivo.",
    "OBJETO_NO_SUBIDO": "Aún no recibimos el archivo. Súbelo e intenta de nuevo.",
    "ARCHIVO_NO_ENCONTRADO": "Archivo no encontrado.",
    "ARCHIVO_NO_DISPONIBLE": "El archivo aún no está disponible.",
    "PROPIETARIO_INVALIDO": "Indica a quién pertenece el archivo.",
    "CONSENTIMIENTO_REQUERIDO": "Este archivo requiere tu autorización vigente.",
}
MENSAJE_ARCHIVO_GENERICO = "No pudimos procesar el archivo."
MENSAJE_ARCHIVOS_NO_DISPONIBLES = "No pudimos procesar el archivo. Intenta de nuevo."


def _error_archivo(codigo: str) -> ErrorArchivo:
    return ErrorArchivo(
        codigo=codigo, mensaje=MENSAJES_ARCHIVO.get(codigo, MENSAJE_ARCHIVO_GENERICO)
    )


def _almacenamiento(info: Info):
    return info.context["clientes"].almacenamiento


# ---------- Onboarding de identidad (HU-M01, v0.20) ----------


@strawberry.enum
class EstadoOnboarding(Enum):
    INICIADO = "INICIADO"
    EN_VERIFICACION = "EN_VERIFICACION"
    VERIFICADA = "VERIFICADA"
    RECHAZADA = "RECHAZADA"
    EN_REVISION = "EN_REVISION"


@strawberry.enum
class MotivoOnboarding(Enum):
    ILEGIBLE = "ILEGIBLE"
    SUPLANTACION = "SUPLANTACION"
    ROSTRO_NO_COINCIDE = "ROSTRO_NO_COINCIDE"
    MENOR_DE_EDAD = "MENOR_DE_EDAD"
    DOCUMENTO_YA_REGISTRADO = "DOCUMENTO_YA_REGISTRADO"
    PROVEEDOR_NO_DISPONIBLE = "PROVEEDOR_NO_DISPONIBLE"
    IMAGEN_NO_DISPONIBLE = "IMAGEN_NO_DISPONIBLE"


@strawberry.enum
class PasoOnboarding(Enum):
    ANVERSO = "ANVERSO"
    REVERSO = "REVERSO"
    ROSTRO = "ROSTRO"
    VIDEO = "VIDEO"


@strawberry.enum
class TipoDocumentoOnboarding(Enum):
    CC = "CC"
    CE = "CE"


@strawberry.type
class Onboarding:
    id: strawberry.ID
    estado: EstadoOnboarding
    motivo: MotivoOnboarding | None
    intento: int
    intentos_restantes: int


@strawberry.type
class InicioOnboarding:
    onboarding: Onboarding
    token: str
    token_vence_en: datetime


@strawberry.type
class CuentaOnboarding:
    sesion: Sesion
    onboarding: Onboarding


@strawberry.type
class CodigoOnboardingEnviado:
    correo_enmascarado: str
    vence_en: datetime


@strawberry.type
class ErrorOnboarding:
    codigo: str
    mensaje: str


ResultadoInicioOnboarding = Annotated[
    InicioOnboarding | ErrorOnboarding, strawberry.union("ResultadoInicioOnboarding")
]
ResultadoOnboarding = Annotated[
    Onboarding | ErrorOnboarding, strawberry.union("ResultadoOnboarding")
]
ResultadoSubidaOnboarding = Annotated[
    SubidaArchivo | ErrorOnboarding, strawberry.union("ResultadoSubidaOnboarding")
]
ResultadoCuentaOnboarding = Annotated[
    CuentaOnboarding | ErrorOnboarding, strawberry.union("ResultadoCuentaOnboarding")
]
ResultadoCodigoOnboarding = Annotated[
    CodigoOnboardingEnviado | ErrorOnboarding, strawberry.union("ResultadoCodigoOnboarding")
]


@strawberry.input
class IniciarOnboardingInput:
    tipo_documento: TipoDocumentoOnboarding
    jurisdiccion: str = "CO"


@strawberry.input
class CuentaOnboardingInput:
    correo: str  # dato personal: solo en el cuerpo hacia AUTH, nunca se loguea
    clave: str


# Finalidad del consentimiento de la biometría (regla 7): se registra en AUTH antes de capturar.
FINALIDAD_ONBOARDING = "onboarding-biometrico"
CANAL_APP = "app"
MENSAJES_ONBOARDING = {
    "TOKEN_INVALIDO": "Tu verificación venció o no es válida. Empieza de nuevo.",
    "SESION_NO_ENCONTRADA": "Tu verificación venció o no es válida. Empieza de nuevo.",
    "SIN_CONSENTIMIENTO": "Primero acepta el tratamiento de tus datos biométricos.",
    "ESTADO_INVALIDO": "Este paso ya no aplica a tu verificación.",
    "PIEZAS_INCOMPLETAS": "Faltan fotos o el video. Complétalos e intenta de nuevo.",
    "SIN_INTENTOS": "Ya no quedan intentos de verificación.",
    "SESION_CON_CUENTA": "Esta verificación ya tiene una cuenta.",
    "CORREO_YA_REGISTRADO": "Ya existe una cuenta con ese correo.",
    "DATO_INVALIDO": "Revisa los datos e intenta de nuevo.",
    "CODIGO_INVALIDO": "El código no es correcto. Revísalo e intenta de nuevo.",
    "CODIGO_VENCIDO": "El código venció. Pide uno nuevo.",
    "INTENTOS_AGOTADOS": "Agotaste los intentos. Pide un código nuevo.",
    "JURISDICCION_NO_SOPORTADA": "Aún no verificamos documentos de ese país.",
    "TIPO_DOCUMENTO_NO_SOPORTADO": "Ese tipo de documento no se admite.",
    "PASO_INVALIDO": "Ese paso no existe.",
    "ARCHIVO_NO_ENCONTRADO": "No encontramos ese archivo. Súbelo de nuevo.",
    "NO_DISPONIBLE": "No pudimos continuar tu verificación. Intenta de nuevo.",
}


def _error_onboarding(codigo: str) -> ErrorOnboarding:
    mensaje = MENSAJES_ONBOARDING.get(codigo) or MENSAJES_ARCHIVO.get(
        codigo, MENSAJES_ONBOARDING["NO_DISPONIBLE"]
    )
    return ErrorOnboarding(codigo=codigo, mensaje=mensaje)


def _onboarding_de(datos: dict) -> Onboarding:
    motivo = datos.get("motivo")
    return Onboarding(
        id=strawberry.ID(datos["id"]),
        estado=EstadoOnboarding(datos["estado"]),
        motivo=MotivoOnboarding(motivo) if motivo in MotivoOnboarding._value2member_map_ else None,
        intento=datos["intento"],
        intentos_restantes=datos["intentos_restantes"],
    )


def _onboarding(info: Info):
    return info.context["clientes"].onboarding


def _token_onboarding(info: Info) -> str:
    """El token del recorrido; sin él responde el mismo TOKEN_INVALIDO que un token ajeno."""
    token = info.context.get("token_onboarding")
    if not token:
        raise ErrorOnboardingServicio("TOKEN_INVALIDO")
    return token


async def _paso_onboarding(llamada) -> Onboarding | ErrorOnboarding:
    """Ejecuta una llamada al servicio y traduce su resultado o su rechazo (nunca un 5xx)."""
    try:
        return _onboarding_de(await llamada())
    except ErrorOnboardingServicio as error:
        return _error_onboarding(error.codigo)
    except ErrorServicio:
        return _error_onboarding("NO_DISPONIBLE")


def _sesion(datos: dict) -> Sesion:
    return Sesion(
        token=datos["token"],
        expira_en=datetime.fromisoformat(datos["expira_en"]),
        usuario=_usuario(datos["usuario"]),
    )


# ---------- Siniestros (v0.21, HU-W04, servicio CLAIMS) ----------


@strawberry.enum
class EstadoSiniestro(Enum):
    PENDIENTE = "PENDIENTE"
    EN_REVISION = "EN_REVISION"  # también un siniestro derivado a perito
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


@strawberry.enum
class TipoEvidencia(Enum):
    FOTO = "FOTO"
    DOCUMENTO = "DOCUMENTO"


@strawberry.enum
class TipoResolucion(Enum):
    APROBACION = "APROBACION"
    RECHAZO = "RECHAZO"
    DERIVACION = "DERIVACION"


@strawberry.enum
class MotivoRechazo(Enum):
    EVIDENCIA_INSUFICIENTE = "EVIDENCIA_INSUFICIENTE"
    FUERA_DE_COBERTURA = "FUERA_DE_COBERTURA"
    POLIZA_NO_VIGENTE = "POLIZA_NO_VIGENTE"
    INCONSISTENCIA_EN_RELATO = "INCONSISTENCIA_EN_RELATO"
    OTRO = "OTRO"


@strawberry.type
class Asegurado:
    nombre: str
    documento: str


@strawberry.type
class Ubicacion:
    descripcion: str
    ciudad: str
    latitud: float
    longitud: float


@strawberry.type
class Evidencia:
    id: strawberry.ID
    tipo: TipoEvidencia
    titulo: str
    archivo: str
    registrada_en: datetime
    geoetiquetada: bool
    nota: str | None
    archivo_id: strawberry.Private[str]

    @strawberry.field
    async def url(self, info: Info) -> str:
        """W04-5: URL firmada de lectura (minutos) que se pide al almacenamiento **como el
        BACKOFFICE que consulta**, para que cada acceso quede auditado. Solo se firma si la consulta
        pide el campo. Si el archivo no está disponible, vacía: la web muestra «Imagen no
        disponible» y nunca un error técnico."""
        usuario = info.context.get("usuario")
        try:
            descarga = await _almacenamiento(info).descarga(
                usuario.rol, usuario.id, self.archivo_id
            )
        except (ErrorServicio, ArchivoRechazado):
            logger.warning("evidencia=%s sin URL de lectura", id_opaco(str(self.id)))
            return ""
        return descarga["url"]


@strawberry.type
class Verificacion:
    descripcion: str
    cumple: bool


@strawberry.type
class Perito:
    id: strawberry.ID
    nombre: str
    ciudad: str
    red: str
    registro: str
    tiempo_respuesta_dias: int
    vencimiento_informe: datetime | None  # solo en `peritos(siniestroId)`


@strawberry.type
class Resolucion:
    tipo: TipoResolucion
    resuelta_en: datetime
    notificacion: str
    motivo: MotivoRechazo | None
    comentario: str | None
    via_recurso: str | None
    indemnizacion: Monto | None
    metodo_transferencia: str | None
    plazo_dispersion: str | None
    perito: Perito | None
    instrucciones: str | None
    vencimiento_informe: datetime | None
    automatica: bool  # v0.23: la aprobó el sistema porque el plazo venció sin gestión


@strawberry.type
class Siniestro:
    id: strawberry.ID
    radicado: str
    estado: EstadoSiniestro
    asegurado: Asegurado
    poliza_id: strawberry.Private[str]
    cobertura_aplicada: str
    entidad_acreedora: str
    ocurrido_en: datetime
    notificado_en: datetime
    ubicacion: Ubicacion
    descripcion: str
    suma_reclamada: Monto
    vence_dictamen_en: datetime
    evidencias: list[Evidencia]
    verificaciones: list[Verificacion]
    resolucion: Resolucion | None

    @strawberry.field
    async def poliza(self, info: Info) -> Poliza:
        """La póliza la escribe POLICY (W04-4): se pide solo si la consulta la usa, una vez por
        póliza y por petición."""
        return _poliza(await _poliza_del_siniestro(info, self.poliza_id))


@strawberry.type
class ErrorSiniestro:
    """Error de negocio en lenguaje del operador.

    Solo `mensaje`: la web distingue la union por la forma de la respuesta.
    """

    mensaje: str


ResultadoResolucion = Annotated[Siniestro | ErrorSiniestro, strawberry.union("ResultadoResolucion")]

MENSAJE_SINIESTROS = "No pudimos consultar los siniestros. Intenta de nuevo."

# ---------- Estado del sistema (v0.22, HU-W07) ----------


@strawberry.enum
class ColorSalud(Enum):
    VERDE = "VERDE"
    AMARILLO = "AMARILLO"
    ROJO = "ROJO"


@strawberry.enum
class EstadoCircuito(Enum):
    CERRADO = "CERRADO"
    SEMIABIERTO = "SEMIABIERTO"
    ABIERTO = "ABIERTO"
    DESCONOCIDO = "DESCONOCIDO"


@strawberry.type
class SaludMicro:
    nombre: str
    peso: int
    porcentaje: int
    color: ColorSalud
    responde: bool
    sin_trafico: bool
    disponibilidad: float | None = None
    latencia_media_ms: int | None = None
    latencia_p95_ms: int | None = None


@strawberry.type
class SaludExterna:
    nombre: str
    estado: EstadoCircuito


@strawberry.type
class SaludApi:
    nombre: str
    porcentaje: int
    color: ColorSalud
    micros: list[str]
    externas: list[SaludExterna]


@strawberry.type
class EstadoSistema:
    porcentaje: int
    color: ColorSalud
    ventana: str
    calculado_en: datetime
    apis: list[SaludApi]
    micros: list[SaludMicro]
    disponibilidad: float | None = None
    latencia_media_ms: int | None = None


MENSAJE_ESTADO_SISTEMA = "No pudimos calcular el estado del sistema. Intenta de nuevo."
MENSAJE_VENTANA_INVALIDA = "La ventana debe ser 15m, 1h, 24h o 7d."
MENSAJE_POLIZA_SINIESTRO = "No pudimos consultar la póliza del siniestro. Intenta de nuevo."
MENSAJE_ACCION_SINIESTRO = "No pudimos completar la acción sobre el siniestro. Intenta de nuevo."


def _claims(info: Info):
    return info.context["clientes"].claims


async def _poliza_del_siniestro(info: Info, poliza_id: str) -> dict:
    """Una sola llamada a POLICY por póliza y por petición, aunque varios siniestros la
    compartan."""
    cache = info.context.setdefault("_polizas_siniestros", {})
    if poliza_id not in cache:
        policy = info.context["clientes"].policy
        cache[poliza_id] = asyncio.ensure_future(policy.obtener_poliza(poliza_id))
    try:
        datos = await cache[poliza_id]
    except ErrorServicio as error:
        raise ErrorPublico(MENSAJE_POLIZA_SINIESTRO) from error
    if datos is None:
        raise ErrorPublico(MENSAJE_POLIZA_SINIESTRO)
    return datos


def _monto_siniestro(datos: dict | None) -> Monto | None:
    return Monto(valor=str(datos["valor"]), moneda=datos["moneda"]) if datos else None


def _perito(datos: dict | None) -> Perito | None:
    if not datos:
        return None
    return Perito(
        id=strawberry.ID(datos["id"]),
        nombre=datos["nombre"],
        ciudad=datos["ciudad"],
        red=datos["red"],
        registro=datos["registro"],
        tiempo_respuesta_dias=datos["tiempo_respuesta_dias"],
        vencimiento_informe=_fecha(datos.get("vencimiento_informe")),
    )


def _resolucion(datos: dict | None) -> Resolucion | None:
    if not datos:
        return None
    return Resolucion(
        tipo=TipoResolucion(datos["tipo"]),
        resuelta_en=_fecha(datos["resuelta_en"]),
        notificacion=datos["notificacion"],
        motivo=MotivoRechazo(datos["motivo"]) if datos.get("motivo") else None,
        comentario=datos.get("comentario"),
        via_recurso=datos.get("via_recurso"),
        indemnizacion=_monto_siniestro(datos.get("indemnizacion")),
        metodo_transferencia=datos.get("metodo_transferencia"),
        plazo_dispersion=datos.get("plazo_dispersion"),
        perito=_perito(datos.get("perito")),
        instrucciones=datos.get("instrucciones"),
        vencimiento_informe=_fecha(datos.get("vencimiento_informe")),
        automatica=datos.get("automatica", False),
    )


def _siniestro(datos: dict) -> Siniestro:
    """El expediente que arma CLAIMS (snake_case) en los tipos del contrato v0.21."""
    return Siniestro(
        id=strawberry.ID(datos["id"]),
        radicado=datos["radicado"],
        estado=EstadoSiniestro(datos["estado"]),
        asegurado=Asegurado(
            nombre=datos["asegurado"]["nombre"], documento=datos["asegurado"]["documento"]
        ),
        poliza_id=datos["poliza_id"],
        cobertura_aplicada=datos["cobertura_aplicada"],
        entidad_acreedora=datos["entidad_acreedora"],
        ocurrido_en=_fecha(datos["ocurrido_en"]),
        notificado_en=_fecha(datos["notificado_en"]),
        ubicacion=Ubicacion(
            descripcion=datos["ubicacion"]["descripcion"],
            ciudad=datos["ubicacion"]["ciudad"],
            latitud=float(datos["ubicacion"]["latitud"]),
            longitud=float(datos["ubicacion"]["longitud"]),
        ),
        descripcion=datos["descripcion"],
        suma_reclamada=_monto_siniestro(datos["suma_reclamada"]),
        vence_dictamen_en=_fecha(datos["vence_dictamen_en"]),
        evidencias=[
            Evidencia(
                id=strawberry.ID(e["id"]),
                tipo=TipoEvidencia(e["tipo"]),
                titulo=e["titulo"],
                archivo=e["archivo"],
                registrada_en=_fecha(e["registrada_en"]),
                geoetiquetada=e["geoetiquetada"],
                nota=e.get("nota"),
                archivo_id=e["archivo_id"],
            )
            for e in datos["evidencias"]
        ],
        verificaciones=[
            Verificacion(descripcion=v["descripcion"], cumple=v["cumple"])
            for v in datos["verificaciones"]
        ],
        resolucion=_resolucion(datos.get("resolucion")),
    )


async def _resolver_siniestro(accion) -> ResultadoResolucion:
    try:
        return _siniestro(await accion())
    except ResolucionRechazada as error:
        return ErrorSiniestro(mensaje=error.mensaje)
    except ErrorServicio:
        return ErrorSiniestro(mensaje=MENSAJE_ACCION_SINIESTRO)


# ---------- Operaciones ----------


CLIENTE_O_ASESOR = ("CLIENTE", "ASESOR")
SOLO_BACKOFFICE = ("BACKOFFICE",)
SOLO_CLIENTE = ("CLIENTE",)
MENSAJE_AUTH_NO_DISPONIBLE = "No pudimos procesar la solicitud. Intenta de nuevo."
MENSAJE_USUARIO_NO_ENCONTRADO = "Usuario no encontrado."
MENSAJE_COBROS_NO_DISPONIBLES = "No pudimos consultar los cobros. Intenta de nuevo."
MENSAJE_DOCUMENTO_REQUERIDO = "Indica el documento de identidad del titular."
TODOS_LOS_ROLES = ("CLIENTE", "ASESOR", "BACKOFFICE")


def _auth(info: Info):
    return info.context["clientes"].auth


async def _nombres_de_clientes(info: Info, cobros: list[dict]) -> dict[str, str]:
    """Pide a AUTH los nombres de los clientes de estos cobros, todos juntos (no uno por
    cobro) y de a 200, el máximo de AUTH. Si AUTH falla, los cobros salen sin nombre: ver el
    cobro importa más que ver el nombre. El nombre es dato personal: ni él ni los ids se
    loguean; solo cuántos clientes se consultaron y quién consultó (auditoría)."""
    ids = sorted({c["cliente_id"] for c in cobros if c.get("cliente_id")})
    if not ids:
        return {}
    usuario = info.context.get("usuario")
    logger.info(
        "nombres de clientes consultados actor=%s clientes=%d",
        id_opaco(usuario.id) if usuario else "-",
        len(ids),
    )
    nombres: dict[str, str] = {}
    try:
        for i in range(0, len(ids), TAMANO_LOTE_NOMBRES):
            nombres |= await _auth(info).nombres_de_clientes(ids[i : i + TAMANO_LOTE_NOMBRES])
    except (ErrorServicio, SolicitudInvalida) as error:
        logger.warning("nombres de clientes no disponibles error=%s", type(error).__name__)
    return nombres | await _nombres_de_titulares(info, cobros, nombres)


async def _nombres_de_titulares(
    info: Info, cobros: list[dict], conocidos: dict[str, str]
) -> dict[str, str]:
    """El titular que dio un ASESOR no tiene cuenta: su nombre está en la propuesta de POLICY, y
    `poliza_id` es el id de la propuesta. Si POLICY falla, esos cobros salen sin nombre."""
    ids = sorted(
        {c["poliza_id"] for c in cobros if c.get("cliente_id") and c["cliente_id"] not in conocidos}
    )
    nombres: dict[str, str] = {}
    try:
        for i in range(0, len(ids), TAMANO_LOTE_NOMBRES):
            nombres |= await info.context["clientes"].policy.nombres_titulares(
                ids[i : i + TAMANO_LOTE_NOMBRES]
            )
    except ErrorServicio as error:
        logger.warning("nombres de titulares no disponibles error=%s", type(error).__name__)
    return nombres


def _usuario_de_la_pagina_de_firma(info: Info) -> UsuarioActual | None:
    """Quién entra a la página de firma (W03-33). Con el token del enlace no hace falta sesión (y
    una sesión vencida que viaje con él no estorba); sin él se exige la sesión de un CLIENTE."""
    if info.context.get("enlace_firma"):
        usuario = None if info.context["error_token"] else info.context["usuario"]
        return usuario if usuario is not None and usuario.rol == "CLIENTE" else None
    return exigir_rol(info, *SOLO_CLIENTE, siempre=True)


def _firma_pago(info: Info) -> ServicioFirmaPago:
    return info.context["servicio_firma_pago"]


def _en_enum(enum: type[Enum], motivo: str | None) -> Enum | None:
    return enum(motivo) if motivo in {m.value for m in enum} else None


def _error_codigo(error: ErrorFlujoDatos) -> ErrorCodigo:
    motivo = "PROPUESTA_NO_VALIDA" if error.motivo == "PROPUESTA_NO_ENCONTRADA" else error.motivo
    return ErrorCodigo(
        mensaje=error.mensaje,
        puede_reintentar=error.puede_reintentar,
        motivo=_en_enum(MotivoErrorCodigo, motivo),
        intentos_restantes=error.extra.get("intentos_restantes"),
    )


def _error_firma(error: ErrorFlujoDatos) -> ErrorFirma:
    motivo = error.motivo
    if motivo in ("PROPUESTA_NO_ENCONTRADA", "PROPUESTA_NO_VALIDA"):
        motivo = "PROPUESTA_NO_FIRMABLE"
    elif motivo is None:
        motivo = "SERVICIO_FIRMA_NO_DISPONIBLE"
    return ErrorFirma(
        mensaje=error.mensaje,
        puede_reintentar=error.puede_reintentar,
        motivo=_en_enum(MotivoErrorFirma, motivo),
    )


def _error_pago(error: ErrorFlujoDatos) -> ErrorPago:
    return ErrorPago(
        mensaje=error.mensaje,
        puede_reintentar=error.puede_reintentar,
        motivo=_en_enum(MotivoErrorPago, error.motivo),
        fallos_seguidos=error.extra.get("fallos"),
    )


def _resultado_emision(resultado: dict | ErrorFlujoDatos) -> ResultadoEmision:
    if isinstance(resultado, ErrorFlujoDatos):
        return ErrorEmision(mensaje=resultado.mensaje, puede_reintentar=resultado.puede_reintentar)
    propuesta, enlace = _propuesta(resultado["propuesta"]), resultado.get("enlace")
    if enlace is None:
        return propuesta
    return EnlaceEnviado(
        propuesta=propuesta,
        correo_enmascarado=enlace["correo_enmascarado"],
        vence_en=_fecha(enlace["vence_en"]),
    )


def _nulo_o_error_publico(error: ErrorFlujoDatos) -> None:
    """Una consulta no tiene unión de error: lo inexistente es `null`; una falla del servicio, un
    error con reintento."""
    if error.motivo is None:
        raise ErrorPublico(error.mensaje)
    return None


# Cómo se nombra cada canal en el resumen de `contactarAsesor` (CA-W01-07).
RESUMEN_CANAL = {
    CanalContacto.LLAMAR: "llamada",
    CanalContacto.CHAT: "chat",
    CanalContacto.AGENDAR: "llamada agendada",
}


@strawberry.type
class Query:
    @strawberry.field
    async def archivo(self, info: Info, id: strawberry.ID) -> Archivo | None:
        """KAN-250: el archivo si existe y quien pregunta puede leerlo (decide ALMACENAMIENTO)."""
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _almacenamiento(info).obtener(usuario.rol, usuario.id, str(id))
        except ArchivoRechazado:
            return None
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_ARCHIVOS_NO_DISPONIBLES) from error
        return _archivo(datos) if datos else None

    @strawberry.field
    async def oferta(self, info: Info, id: strawberry.ID) -> Oferta | None:
        """La oferta que RATING guardó (CA-W03-01), p. ej. para «ver fuentes» al aceptarla. `null`
        si venció o no existe, o si no es del cliente o ya no tiene consentimiento vigente."""
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        try:
            datos = await ServicioSuscripcion(info.context["clientes"]).obtener_oferta(
                str(id), usuario
            )
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SUSCRIPCION_NO_DISPONIBLE) from error
        return _oferta(datos) if datos else None

    @strawberry.field
    async def propuesta(self, info: Info, id: strawberry.ID) -> Propuesta | None:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        try:
            datos = await ServicioSuscripcion(info.context["clientes"]).obtener_propuesta(
                str(id), usuario
            )
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SUSCRIPCION_NO_DISPONIBLE) from error
        return _propuesta(datos) if datos else None

    @strawberry.field
    async def documento_firma(
        self, info: Info, propuesta_id: strawberry.ID
    ) -> DocumentoFirma | None:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        try:
            datos = await ServicioSuscripcion(info.context["clientes"]).documento_firma(
                str(propuesta_id), usuario
            )
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SUSCRIPCION_NO_DISPONIBLE) from error
        return _documento_firma(datos) if datos else None

    @strawberry.field
    async def poliza(self, info: Info, id: strawberry.ID) -> Poliza | None:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        try:
            datos = await ServicioSuscripcion(info.context["clientes"]).obtener_poliza(
                str(id), usuario
            )
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SUSCRIPCION_NO_DISPONIBLE) from error
        return _poliza(datos) if datos else None

    @strawberry.field
    async def propuestas(
        self,
        info: Info,
        estado: EstadoVisible | None = strawberry.UNSET,
        pagina: int | None = 1,
        tamano: int | None = 10,
    ) -> PaginaPropuestas:
        """W03-35: filtradas por el rol del token (nunca por un argumento): CLIENTE las de su
        titular, ASESOR las que aceptó y BACKOFFICE todas. Los datos personales salen solo para
        ASESOR y BACKOFFICE."""
        usuario = exigir_rol(info, *TODOS_LOS_ROLES, siempre=True)
        filtro = estado.value if isinstance(estado, EstadoVisible) else None
        servicio = _firma_pago(info)
        try:
            datos = await servicio.listar_propuestas(
                usuario, filtro, max(1, pagina or 1), max(1, min(tamano or 10, 50))
            )
            asesores, socios = (
                await asyncio.gather(
                    servicio.nombres_de_asesores(datos["propuestas"]),
                    servicio.nombres_de_socios(datos["propuestas"]),
                )
                if usuario.rol == "BACKOFFICE"
                else ({}, {})
            )
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_PROPUESTAS_NO_DISPONIBLES) from error
        ve_personales = usuario.rol != "CLIENTE"
        return PaginaPropuestas(
            propuestas=[
                _propuesta(
                    p,
                    poliza=_poliza_de_resumen(p.get("poliza"), p),
                    nombre_titular=p.get("nombre_titular") if ve_personales else None,
                    nombre_asesor=asesores.get(p.get("actor_usuario_id") or ""),
                    nombre_socio=(
                        socios.get(p.get("actor_usuario_id") or "")
                        if p.get("actor_rol") == "SOCIO"
                        else None
                    ),
                )
                for p in datos["propuestas"]
            ],
            total=datos["total"],
            pagina=datos["pagina"],
            tamano=datos["tamano"],
        )

    @strawberry.field
    async def firma_por_enlace(
        self, info: Info, propuesta_id: strawberry.ID | None = None
    ) -> EnlaceFirma | None:
        """W03-33: el estado de la página de firma. Token del enlace (`X-Enlace-Firma`) o sesión de
        CLIENTE con `propuestaId`. Sin acceso a esa propuesta, `null`: no se revela si existe."""
        usuario = _usuario_de_la_pagina_de_firma(info)
        servicio = _firma_pago(info)
        autorizado = await servicio.autorizar_enlace(
            usuario, info.context.get("enlace_firma"), str(propuesta_id) if propuesta_id else None
        )
        if isinstance(autorizado, ErrorFlujoDatos):
            return _nulo_o_error_publico(autorizado)
        datos = await servicio.estado_enlace(autorizado)
        if isinstance(datos, ErrorFlujoDatos):
            return _nulo_o_error_publico(datos)
        firma = datos.get("firma")
        propuesta = datos["propuesta"]
        poliza = datos.get("poliza")
        return EnlaceFirma(
            # Con sesión de CLIENTE no hay enlace: siempre vigente y sin vencimiento.
            vigente=datos["vigente"] or autorizado.acceso.enlace_token is None,
            vence_en=_fecha(datos.get("vence_en")) if autorizado.acceso.enlace_token else None,
            propuesta=_propuesta(propuesta),
            documento=_documento_firma(datos["documento"]) if datos.get("documento") else None,
            documento_url=(
                f"/propuestas/{propuesta['id']}/contrato.pdf" if datos.get("documento") else None
            ),
            firma=FirmaRegistrada(
                propuesta_id=propuesta["id"],
                firmada_en=_fecha(firma["firmada_en"]),
                referencia=firma["referencia"],
                estado=EstadoPropuesta(propuesta["estado"]),
            )
            if firma
            else None,
            poliza=_poliza(poliza) if poliza else None,
        )

    @strawberry.field
    async def siniestros(
        self, info: Info, estado: EstadoSiniestro | None = None
    ) -> list[Siniestro]:
        """HU-W04: bandeja de la mesa de siniestros (solo BACKOFFICE, W04-11). CLAIMS la lee de
        la réplica (HA-LAT-003)."""
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _claims(info).listar(estado.value if estado else None)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SINIESTROS) from error
        return [_siniestro(d) for d in datos]

    @strawberry.field
    async def siniestro(self, info: Info, id: strawberry.ID) -> Siniestro | None:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _claims(info).obtener(str(id))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SINIESTROS) from error
        return _siniestro(datos) if datos else None

    @strawberry.field
    async def peritos(self, info: Info, siniestro_id: strawberry.ID) -> list[Perito]:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _claims(info).peritos(str(siniestro_id))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_SINIESTROS) from error
        return [_perito(p) for p in datos]

    @strawberry.field
    async def estado_sistema(self, info: Info, ventana: str | None = "1h") -> EstadoSistema:
        """v0.22 (HU-W07), solo BACKOFFICE: la salud general, por API y por micro
        (`app/salud.py`)."""
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        salud = info.context.get("salud")
        ventana = ventana or "1h"
        if salud is None:
            raise ErrorPublico(MENSAJE_ESTADO_SISTEMA)
        if ventana not in salud.ventanas:
            raise ErrorPublico(MENSAJE_VENTANA_INVALIDA)
        try:
            datos = await salud.calcular(ventana)
        except Exception as error:  # noqa: BLE001 - nunca un 5xx crudo (regla 4)
            raise ErrorPublico(MENSAJE_ESTADO_SISTEMA) from error
        return EstadoSistema(
            porcentaje=datos["porcentaje"],
            color=ColorSalud(datos["color"]),
            ventana=datos["ventana"],
            calculado_en=datos["calculado_en"],
            disponibilidad=datos["disponibilidad"],
            latencia_media_ms=datos["latencia_media_ms"],
            apis=[
                SaludApi(
                    nombre=a["nombre"],
                    porcentaje=a["porcentaje"],
                    color=ColorSalud(a["color"]),
                    micros=a["micros"],
                    externas=[
                        SaludExterna(nombre=x["nombre"], estado=EstadoCircuito(x["estado"]))
                        for x in a["externas"]
                    ],
                )
                for a in datos["apis"]
            ],
            micros=[
                SaludMicro(
                    nombre=m["nombre"],
                    peso=m["peso"],
                    porcentaje=m["porcentaje"],
                    color=ColorSalud(m["color"]),
                    responde=m["responde"],
                    sin_trafico=m["sin_trafico"],
                    disponibilidad=m["disponibilidad"],
                    latencia_media_ms=m["latencia_media_ms"],
                    latencia_p95_ms=m["latencia_p95_ms"],
                )
                for m in datos["micros"]
            ],
        )

    @strawberry.field
    async def cobros(
        self,
        info: Info,
        poliza_id: strawberry.ID | None = strawberry.UNSET,
        estado: EstadoCobro | None = strawberry.UNSET,
        pagina: int | None = 1,
        tamano: int | None = 10,
    ) -> list[Cobro]:
        """Tabla del back-office (CA-W05-01). Siempre exige token: son datos de pagos."""
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        filtro_poliza = str(poliza_id) if isinstance(poliza_id, str) else None
        filtro_estado = estado.value.lower() if isinstance(estado, EstadoCobro) else None
        try:
            datos = await info.context["clientes"].payments.listar_cobros(
                filtro_poliza, filtro_estado, pagina, tamano
            )
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_COBROS_NO_DISPONIBLES) from error
        nombres = await _nombres_de_clientes(info, datos)
        return [_cobro(c, nombres) for c in datos]

    @strawberry.field
    async def onboarding(self, info: Info, id: strawberry.ID) -> Onboarding | None:
        """v0.20 (HU-M01): con `X-Onboarding-Token`; null si el token no es de ese recorrido."""
        token = info.context.get("token_onboarding")
        if not token:
            return None
        try:
            return _onboarding_de(await _onboarding(info).consultar(str(id), token))
        except ErrorOnboardingServicio:
            return None
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJES_ONBOARDING["NO_DISPONIBLE"]) from error

    @strawberry.field
    async def mi_verificacion(self, info: Info) -> Onboarding | None:
        """v0.20 (HU-M01): el recorrido más reciente de la cuenta del token."""
        usuario = exigir_rol(info, *SOLO_CLIENTE, siempre=True, admite_pendiente=True)
        try:
            datos = await _onboarding(info).de_usuario(usuario.id)
        except (ErrorServicio, ErrorOnboardingServicio) as error:
            raise ErrorPublico(MENSAJES_ONBOARDING["NO_DISPONIBLE"]) from error
        return _onboarding_de(datos) if datos else None

    @strawberry.field
    async def yo(self, info: Info) -> Usuario | None:
        usuario = exigir_rol(
            info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True, admite_pendiente=True
        )
        try:
            datos = await _auth(info).obtener_usuario(usuario.id)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        return _usuario(datos) if datos else None

    @strawberry.field
    def jurisdicciones(self) -> list[Jurisdiccion]:
        """v0.25 (T18): plazo mínimo y máximo, moneda y tipo de documento por país. Son
        parámetros del producto, sin datos personales."""
        return [
            Jurisdiccion(
                codigo=j.codigo,
                nombre=j.nombre,
                moneda=j.moneda,
                tipo_documento=j.tipo_documento,
                plazo_min=j.plazo_min,
                plazo_max=j.plazo_max,
            )
            for j in JURISDICCIONES
        ]

    @strawberry.field
    async def convertir_montos(
        self, info: Info, montos: list[MontoInput], moneda: str
    ) -> list[Monto]:
        """KAN-209: los montos en la moneda que el usuario eligió en el header, con las tasas
        del ACL Worker. Solo para mostrar: no recalcula nada ni cambia lo cobrado."""
        exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        if len(montos) > MAXIMO_MONTOS:
            raise ErrorPublico(MENSAJE_DEMASIADOS_MONTOS)
        try:
            tasas = await info.context["clientes"].acl.tasas_cambio()
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_TASAS_NO_DISPONIBLES) from error
        try:
            return [
                Monto(valor=convertir(m.valor, m.moneda, moneda, tasas), moneda=moneda)
                for m in montos
            ]
        except ConversionInvalida as error:
            raise ErrorPublico(str(error)) from error

    @strawberry.field
    async def usuarios(
        self, info: Info, rol: RolUsuario | None = strawberry.UNSET
    ) -> list[Usuario]:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        filtro = rol.value if isinstance(rol, RolUsuario) else None
        try:
            return [_usuario(u) for u in await _auth(info).listar_usuarios(filtro)]
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def registrar_consentimiento(
        self, info: Info, entrada: ConsentimientoInput
    ) -> Consentimiento:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        # Actor (CA-W01-02, CA-W01-11): si un ASESOR consiente, queda registrado que
        # lo hizo en nombre del cliente. Sin sesión (modo `opcional`) va vacío.
        actor = {"rol": usuario.rol, "usuario_id": usuario.id} if usuario else None
        clave_titular = info.context.get("cliente_id_clave", "dev-cliente-id-clave-local")
        # W03-34: como en `solicitarOferta`, un CLIENTE consiente con el documento de su cuenta.
        titular = await documento_del_cliente(
            info.context["clientes"], usuario, entrada.documento_identidad, clave_titular
        )
        if titular.error:
            raise ErrorPublico(titular.error)
        titular_id = (
            cliente_id_desde_documento(titular.documento, clave_titular)
            if titular.documento
            else None
        )
        canal = entrada.canal or info.context.get("canal", "").upper() or None
        try:
            datos = await _auth(info).registrar_consentimiento(
                entrada.finalidad,
                entrada.version_texto,
                actor,
                titular_id=titular_id,
                tipo_dato=entrada.tipo_dato,
                fuente_datos=entrada.fuente_datos,
                canal=canal,
            )
        except ErrorServicio as error:
            raise ErrorPublico(
                "No pudimos registrar el consentimiento. Intenta de nuevo."
            ) from error
        return _consentimiento(datos)

    @strawberry.mutation
    async def revocar_consentimiento(self, info: Info, id: strawberry.ID) -> Consentimiento:
        exigir_rol(info, *CLIENTE_O_ASESOR)
        try:
            datos = await _auth(info).revocar_consentimiento(str(id))
        except ErrorServicio as error:
            raise ErrorPublico("No pudimos revocar el consentimiento. Intenta de nuevo.") from error
        if datos is None:
            raise ErrorPublico("Consentimiento no encontrado.")
        titular_id = datos.get("titular_id")
        if titular_id:
            await avisar_revocacion(
                info.context["clientes"], _firma_pago(info), titular_id, str(id)
            )
        return _consentimiento(datos)

    @strawberry.mutation
    async def solicitar_oferta(self, info: Info, entrada: SolicitudOfertaInput) -> ResultadoOferta:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        # W03-34: un CLIENTE cotiza con el documento de su cuenta (AUTH lo da en memoria); el
        # ASESOR lo escribe siempre.
        titular = await documento_del_cliente(
            info.context["clientes"],
            usuario,
            entrada.documento_identidad,
            info.context["cliente_id_clave"],
        )
        if titular.error or not titular.documento:
            return ErrorOferta(
                mensaje=titular.error or MENSAJE_DOCUMENTO_REQUERIDO,
                puede_reintentar=False,
                motivo=MotivoErrorOferta.DATOS_INSUFICIENTES,
            )
        resultado = await info.context["servicio_oferta"].solicitar(
            str(entrada.consentimiento_id),
            titular.documento,
            entrada.valor_credito,
            entrada.plazo_meses,
            entrada.direccion_inmueble,
        )
        if isinstance(resultado, ErrorOfertaDatos):
            return ErrorOferta(
                mensaje=resultado.mensaje,
                puede_reintentar=resultado.puede_reintentar,
                motivo=MotivoErrorOferta(resultado.motivo) if resultado.motivo else None,
            )
        return _oferta(resultado)

    @strawberry.mutation
    def contactar_asesor(
        self, info: Info, oferta_id: strawberry.ID, canal: CanalContacto
    ) -> ConfirmacionContacto:
        exigir_rol(info, *CLIENTE_O_ASESOR)
        # MOCK (CA-W01-07): no hay servicio de asesores todavía; solo confirma. El resumen no
        # lleva el id de la oferta: no le dice nada al cliente y llega vacío cuando se pide un
        # asesor desde la pantalla de error (W01-03c), donde no hubo oferta.
        return ConfirmacionContacto(
            canal=canal,
            resumen=f"Recibimos tu solicitud de {RESUMEN_CANAL[canal]}. "
            "Un asesor te contactará para continuar tu cotización.",
        )

    @strawberry.mutation
    async def aceptar_oferta(
        self,
        info: Info,
        oferta_id: strawberry.ID,
        contacto: ContactoTitularInput | None = None,
    ) -> ResultadoAceptacion:
        """El `contacto` (nombre y correo del titular) lo da el ASESOR y POLICY lo guarda cifrado;
        el de un CLIENTE sale de AUTH."""
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        resultado = await ServicioSuscripcion(info.context["clientes"]).aceptar(
            str(oferta_id),
            usuario,
            {"nombre": contacto.nombre, "correo": contacto.correo} if contacto else None,
        )
        if isinstance(resultado, ErrorAceptacionDatos):
            return ErrorAceptacion(
                mensaje=resultado.mensaje,
                puede_reintentar=resultado.puede_reintentar,
                motivo=MotivoErrorAceptacion(resultado.motivo) if resultado.motivo else None,
            )
        return _propuesta(resultado)

    @strawberry.mutation
    async def firmar_propuesta(self, info: Info, entrada: FirmarPropuestaInput) -> ResultadoFirma:
        # Solo el CLIENTE titular firma: el no repudio no admite que firme otro (decisión W03-8), y
        # siempre con sesión aunque la bandera de autenticación esté en `opcional`.
        usuario = exigir_rol(info, "CLIENTE", siempre=True)
        resultado = await ServicioSuscripcion(info.context["clientes"]).firmar(
            str(entrada.propuesta_id), entrada.autoriza_cobro, entrada.version_documento, usuario
        )
        if isinstance(resultado, ErrorFirmaDatos):
            return ErrorFirma(
                mensaje=resultado.mensaje,
                puede_reintentar=resultado.puede_reintentar,
                motivo=MotivoErrorFirma(resultado.motivo),
            )
        return FirmaRegistrada(
            propuesta_id=resultado["propuesta_id"],
            firmada_en=_fecha(resultado["firmada_en"]),
            referencia=resultado["referencia"],
            estado=EstadoPropuesta(resultado["estado"]),
        )

    @strawberry.mutation
    async def reintentar_cobro(self, info: Info, cobro_id: strawberry.ID) -> Cobro:
        """CA-W05-02: PAYMENTS concilia con la pasarela y, si hace falta, cobra con la
        misma clave. Sobre un cobro ya cobrado lo devuelve sin cambios."""
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await info.context["clientes"].payments.reintentar_cobro(str(cobro_id))
        except SolicitudInvalida as error:
            raise ErrorPublico(str(error)) from error
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_COBROS_NO_DISPONIBLES) from error
        if datos is None:
            raise ErrorPublico("Cobro no encontrado.")
        # Si es el primer cobro de una propuesta de W03, POLICY emite la póliza cuando ya se cobró
        # (decisión W03-14: lo reabren el back-office y la reconciliación, nunca el cliente).
        await ServicioSuscripcion(info.context["clientes"]).reconciliar_cobro(datos["poliza_id"])
        return _cobro(datos, await _nombres_de_clientes(info, [datos]))

    @strawberry.mutation
    async def crear_cobro(self, info: Info, entrada: CrearCobroInput) -> ResultadoCobro:
        """KAN-28: el back-office ordena un cobro en PAYMENTS. Reenviar el mismo
        `idIntentoCobro` devuelve el mismo cobro sin cobrar de nuevo. Ni el token del
        medio de pago ni la póliza se loguean."""
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await info.context["clientes"].payments.crear_cobro(
                {
                    "id_intento_cobro": str(entrada.id_intento_cobro),
                    "poliza_id": str(entrada.poliza_id),
                    "cliente_id": str(entrada.cliente_id) if entrada.cliente_id else None,
                    "monto": entrada.monto.valor,
                    "moneda": entrada.monto.moneda,
                    "token_medio_pago": entrada.token_medio_pago,
                }
            )
        except SolicitudInvalida as error:
            return ErrorCobro(mensaje=str(error))
        except ErrorServicio as error:
            raise ErrorPublico("No pudimos registrar el cobro. Intenta de nuevo.") from error
        return _cobro(datos, await _nombres_de_clientes(info, [datos]))

    # ---------- Usuarios y sesión (HU-M02) ----------

    @strawberry.mutation
    async def iniciar_sesion(self, info: Info, entrada: CredencialesInput) -> ResultadoSesion:
        """Pública: es la única operación que no exige rol. La clave nunca se loguea."""
        # El canal decide qué recibe una cuenta del onboarding sin verificar (HU-M01): en la app,
        # un token `verificacion-pendiente`; en la web, VERIFICACION_PENDIENTE.
        try:
            sesion = await _auth(info).iniciar_sesion(
                entrada.usuario,
                entrada.clave,
                info.context.get("canal"),
                entrada.rol.value if entrada.rol else None,
            )
        except VerificacionPendiente:
            return ErrorAutenticacion(
                mensaje="Tu identidad aún no está verificada. Ingresa desde la app Solventa.",
                codigo="VERIFICACION_PENDIENTE",
            )
        except ErrorServicio as error:
            raise ErrorPublico("No pudimos iniciar sesión. Intenta de nuevo.") from error
        if sesion is None:
            return ErrorAutenticacion(mensaje="Usuario o clave incorrectos.")
        return _sesion(sesion)

    @strawberry.mutation
    async def crear_usuario(self, info: Info, entrada: CrearUsuarioInput) -> ResultadoUsuario:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _auth(info).crear_usuario(
                entrada.usuario,
                entrada.clave,
                entrada.rol.value,
                entrada.nombre,
                entrada.correo,
                entrada.telefono,
                entrada.documento_identidad,
            )
        except SolicitudInvalida as error:
            return ErrorUsuario(mensaje=str(error))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        return _usuario(datos)

    @strawberry.mutation
    async def cambiar_rol_usuario(
        self, info: Info, id: strawberry.ID, rol: RolUsuario
    ) -> ResultadoUsuario:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _auth(info).cambiar_rol_usuario(str(id), rol.value)
        except SolicitudInvalida as error:
            return ErrorUsuario(mensaje=str(error))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        if datos is None:
            return ErrorUsuario(mensaje=MENSAJE_USUARIO_NO_ENCONTRADO)
        return _usuario(datos)

    @strawberry.mutation
    async def actualizar_preferencias(
        self, info: Info, entrada: PreferenciasInput
    ) -> ResultadoUsuario:
        """KAN-209: idioma y moneda por defecto del propio usuario (el id sale del token)."""
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _auth(info).actualizar_preferencias(
                usuario.id, entrada.idioma, entrada.moneda
            )
        except SolicitudInvalida as error:
            return ErrorUsuario(mensaje=str(error))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        if datos is None:
            return ErrorUsuario(mensaje=MENSAJE_USUARIO_NO_ENCONTRADO)
        return _usuario(datos)

    @strawberry.mutation
    async def actualizar_contacto(self, info: Info, entrada: ContactoInput) -> ResultadoUsuario:
        """HU-W03, bloque 8: correo y teléfono del propio CLIENTE (el id sale del token). AUTH los
        guarda cifrados y la respuesta solo dice `tieneContacto`; el BFF no los registra."""
        usuario = exigir_rol(info, *SOLO_CLIENTE, siempre=True)
        try:
            datos = await _auth(info).actualizar_contacto(
                usuario.id, entrada.correo, entrada.telefono
            )
        except SolicitudInvalida as error:
            return ErrorUsuario(mensaje=str(error))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        if datos is None:
            return ErrorUsuario(mensaje=MENSAJE_USUARIO_NO_ENCONTRADO)
        return _usuario(datos)

    @strawberry.mutation
    async def suspender_usuario(self, info: Info, id: strawberry.ID) -> ResultadoUsuario:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _auth(info).suspender_usuario(str(id))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        if datos is None:
            return ErrorUsuario(mensaje=MENSAJE_USUARIO_NO_ENCONTRADO)
        return _usuario(datos)

    @strawberry.mutation
    async def solicitar_subida_archivo(
        self, info: Info, entrada: SolicitudSubidaArchivoInput
    ) -> ResultadoSubidaArchivo:
        """KAN-250: URL firmada para subir directo al almacén. Un CLIENTE sube siempre a su
        nombre; el resto indica el propietario."""
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        propietario = usuario.id if usuario.rol == "CLIENTE" else entrada.propietario_id
        if not propietario:
            return _error_archivo("PROPIETARIO_INVALIDO")
        solicitud = {
            "tipo": entrada.tipo,
            "tipo_contenido": entrada.tipo_contenido,
            "tamano_bytes": entrada.tamano_bytes,
            "propietario_id": str(propietario),
            "consentimiento_id": str(entrada.consentimiento_id)
            if entrada.consentimiento_id
            else None,
        }
        try:
            datos = await _almacenamiento(info).solicitar_subida(usuario.rol, usuario.id, solicitud)
        except ArchivoRechazado as error:
            return _error_archivo(error.codigo)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_ARCHIVOS_NO_DISPONIBLES) from error
        return SubidaArchivo(
            archivo=_archivo(datos["archivo"]),
            url=datos["url"],
            metodo=datos["metodo"],
            encabezados=[
                EncabezadoSubida(nombre=nombre, valor=valor)
                for nombre, valor in datos["encabezados"].items()
            ],
            vence_en=datetime.fromisoformat(datos["vence_en"]),
        )

    @strawberry.mutation
    async def confirmar_subida_archivo(self, info: Info, id: strawberry.ID) -> ResultadoArchivo:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _almacenamiento(info).confirmar(usuario.rol, usuario.id, str(id))
        except ArchivoRechazado as error:
            return _error_archivo(error.codigo)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_ARCHIVOS_NO_DISPONIBLES) from error
        return _archivo(datos)

    @strawberry.mutation
    async def descargar_archivo(self, info: Info, id: strawberry.ID) -> ResultadoDescargaArchivo:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _almacenamiento(info).descarga(usuario.rol, usuario.id, str(id))
        except ArchivoRechazado as error:
            return _error_archivo(error.codigo)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_ARCHIVOS_NO_DISPONIBLES) from error
        return DescargaArchivo(url=datos["url"], vence_en=datetime.fromisoformat(datos["vence_en"]))

    # ---------- v0.18 (HU-W03, bloque 9, F-B6) ----------

    @strawberry.mutation
    async def enviar_codigo_verificacion(
        self, info: Info, propuesta_id: strawberry.ID, correo: str | None = None
    ) -> ResultadoEnvioCodigo:
        usuario = exigir_rol(info, "ASESOR", siempre=True)
        resultado = await _firma_pago(info).enviar_codigo(usuario, str(propuesta_id), correo)
        if isinstance(resultado, ErrorFlujoDatos):
            return _error_codigo(resultado)
        return CodigoEnviado(
            propuesta_id=resultado["propuesta_id"],
            correo_enmascarado=resultado["correo_enmascarado"],
            vence_en=_fecha(resultado["vence_en"]),
        )

    @strawberry.mutation
    async def verificar_codigo(
        self, info: Info, propuesta_id: strawberry.ID, codigo: str
    ) -> ResultadoVerificacion:
        usuario = exigir_rol(info, "ASESOR", siempre=True)
        resultado = await _firma_pago(info).verificar_codigo(usuario, str(propuesta_id), codigo)
        if isinstance(resultado, ErrorFlujoDatos):
            return _error_codigo(resultado)
        return _propuesta(resultado)

    @strawberry.mutation
    async def emitir_propuesta(self, info: Info, propuesta_id: strawberry.ID) -> ResultadoEmision:
        """ASESOR: «Emitir» (UNDER decide y, si aprueba, sale el enlace de 30 min). CLIENTE:
        «Firmar» (UNDER decide y la propuesta pasa a EN_FIRMA en su sesión)."""
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, siempre=True)
        resultado = await _firma_pago(info).emitir(usuario, str(propuesta_id))
        return _resultado_emision(resultado)

    @strawberry.mutation
    async def reenviar_enlace_firma(
        self, info: Info, propuesta_id: strawberry.ID
    ) -> ResultadoEmision:
        usuario = exigir_rol(info, "ASESOR", siempre=True)
        resultado = await _firma_pago(info).reenviar_enlace(usuario, str(propuesta_id))
        return _resultado_emision(resultado)

    @strawberry.mutation
    async def solicitar_subida_firma(
        self,
        info: Info,
        tipo_contenido: str,
        tamano_bytes: int,
        propuesta_id: strawberry.ID | None = None,
    ) -> ResultadoSubidaArchivo:
        """La URL firmada para subir la imagen de la firma directo al bucket."""
        usuario = _usuario_de_la_pagina_de_firma(info)
        servicio = _firma_pago(info)
        autorizado = await servicio.autorizar_enlace(
            usuario, info.context.get("enlace_firma"), str(propuesta_id) if propuesta_id else None
        )
        if isinstance(autorizado, ErrorFlujoDatos):
            return ErrorArchivo(codigo="NO_DISPONIBLE", mensaje=autorizado.mensaje)
        resultado = await servicio.subida_firma(autorizado, tipo_contenido, tamano_bytes)
        if isinstance(resultado, ErrorFlujoDatos):
            return ErrorArchivo(
                codigo=resultado.motivo or "NO_DISPONIBLE", mensaje=resultado.mensaje
            )
        return SubidaArchivo(
            archivo=_archivo(resultado["archivo"]),
            url=resultado["url"],
            metodo=resultado["metodo"],
            encabezados=[
                EncabezadoSubida(nombre=nombre, valor=valor)
                for nombre, valor in resultado["encabezados"].items()
            ],
            vence_en=datetime.fromisoformat(resultado["vence_en"]),
        )

    @strawberry.mutation
    async def firmar_por_enlace(self, info: Info, entrada: FirmarPorEnlaceInput) -> ResultadoFirma:
        usuario = _usuario_de_la_pagina_de_firma(info)
        servicio = _firma_pago(info)
        autorizado = await servicio.autorizar_enlace(
            usuario,
            info.context.get("enlace_firma"),
            str(entrada.propuesta_id) if entrada.propuesta_id else None,
        )
        if isinstance(autorizado, ErrorFlujoDatos):
            return _error_firma(autorizado)
        resultado = await servicio.firmar(
            autorizado,
            {
                "version_documento": entrada.version_documento,
                "acepta_terminos": entrada.acepta_terminos,
                "autoriza_cobro": entrada.autoriza_cobro,
                "modo": entrada.modo.value,
                "imagen_archivo_id": str(entrada.imagen_archivo_id)
                if entrada.imagen_archivo_id
                else None,
                "nombre_firma": entrada.nombre_firma,
            },
        )
        if isinstance(resultado, ErrorFlujoDatos):
            return _error_firma(resultado)
        return FirmaRegistrada(
            propuesta_id=resultado["propuesta_id"],
            firmada_en=_fecha(resultado["firmada_en"]),
            referencia=resultado["referencia"],
            estado=EstadoPropuesta(resultado["estado"]),
        )

    @strawberry.mutation
    async def iniciar_pago(
        self, info: Info, propuesta_id: strawberry.ID | None = None
    ) -> ResultadoInicioPago:
        usuario = _usuario_de_la_pagina_de_firma(info)
        servicio = _firma_pago(info)
        autorizado = await servicio.autorizar_enlace(
            usuario, info.context.get("enlace_firma"), str(propuesta_id) if propuesta_id else None
        )
        if isinstance(autorizado, ErrorFlujoDatos):
            return _error_pago(autorizado)
        resultado = await servicio.iniciar_pago(autorizado, info.context["idioma"])
        if isinstance(resultado, ErrorFlujoDatos):
            return _error_pago(resultado)
        if "poliza" in resultado:  # ya estaba cobrada y emitida: no hay nada que pagar
            return ErrorPago(mensaje=MENSAJE_POLIZA_YA_EMITIDA, puede_reintentar=False)
        sesion = resultado["sesion"]
        return SesionPago(
            sesion_id=sesion["sesion_id"],
            url_pago=sesion["url_pago"],
            vence_en=_fecha(sesion["vence_en"]),
        )

    @strawberry.mutation
    async def confirmar_pago(
        self, info: Info, sesion_id: strawberry.ID, propuesta_id: strawberry.ID | None = None
    ) -> ResultadoPago:
        """Al regresar de la pasarela. Si se cobró, la póliza sale emitida en la misma respuesta."""
        usuario = _usuario_de_la_pagina_de_firma(info)
        servicio = _firma_pago(info)
        autorizado = await servicio.autorizar_enlace(
            usuario, info.context.get("enlace_firma"), str(propuesta_id) if propuesta_id else None
        )
        if isinstance(autorizado, ErrorFlujoDatos):
            return _error_pago(autorizado)
        resultado = await servicio.confirmar_pago(autorizado, str(sesion_id))
        if isinstance(resultado, ErrorFlujoDatos):
            return _error_pago(resultado)
        return _poliza(resultado["poliza"])

    @strawberry.mutation
    async def ticket_avisos(self, info: Info) -> TicketAvisos:
        usuario = exigir_rol(info, *TODOS_LOS_ROLES, siempre=True)
        try:
            datos = await _firma_pago(info).ticket_avisos(usuario)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AVISOS_NO_DISPONIBLES) from error
        return TicketAvisos(ticket=datos["ticket"], url=datos["url"], vence_en=datos["vence_en"])

    # ---------- v0.20 (HU-M01): onboarding del móvil ----------

    @strawberry.mutation
    async def iniciar_onboarding(
        self, info: Info, entrada: IniciarOnboardingInput
    ) -> ResultadoInicioOnboarding:
        """Inicia el recorrido de verificación de identidad. Si el usuario ya inició sesión
        (CLIENTE en PENDIENTE_VERIFICACION), vincula el onboarding a su cuenta inmediatamente."""
        usuario = exigir_rol(info, "CLIENTE", siempre=False, admite_pendiente=True)
        try:
            datos = await _onboarding(info).iniciar(
                entrada.jurisdiccion.upper(), entrada.tipo_documento.value
            )
            if usuario is not None:
                await _onboarding(info).vincular_cuenta(
                    datos["onboarding_id"], datos["token"], usuario.id
                )
            estado = await _onboarding(info).consultar(datos["onboarding_id"], datos["token"])
        except ErrorOnboardingServicio as error:
            return _error_onboarding(error.codigo)
        except ErrorServicio:
            return _error_onboarding("NO_DISPONIBLE")
        return InicioOnboarding(
            onboarding=_onboarding_de(estado),
            token=datos["token"],
            token_vence_en=datetime.fromisoformat(datos["token_vence_en"]),
        )

    @strawberry.mutation
    async def registrar_consentimiento_onboarding(
        self, info: Info, onboarding_id: strawberry.ID, version_texto: str
    ) -> ResultadoOnboarding:
        """El consentimiento se registra en AUTH (append-only) antes de cualquier captura, y el
        recorrido guarda su id; el storage lo exige en cada subida (regla 7)."""

        async def llamada() -> dict:
            token = _token_onboarding(info)
            servicio = _onboarding(info)
            await servicio.consultar(str(onboarding_id), token)  # el token es de este recorrido
            consentimiento = await _auth(info).registrar_consentimiento(
                FINALIDAD_ONBOARDING, version_texto, None, canal=CANAL_APP.upper()
            )
            return await servicio.registrar_consentimiento(
                str(onboarding_id), token, consentimiento["id"]
            )

        return await _paso_onboarding(llamada)

    @strawberry.mutation
    async def solicitar_subida_onboarding(
        self,
        info: Info,
        onboarding_id: strawberry.ID,
        paso: PasoOnboarding,
        tipo_contenido: str,
        tamano_bytes: int,
    ) -> ResultadoSubidaOnboarding:
        try:
            datos = await _onboarding(info).solicitar_subida(
                str(onboarding_id),
                _token_onboarding(info),
                paso.value,
                tipo_contenido,
                tamano_bytes,
            )
        except ErrorOnboardingServicio as error:
            return _error_onboarding(error.codigo)
        except ErrorServicio:
            return _error_onboarding("NO_DISPONIBLE")
        return SubidaArchivo(
            archivo=Archivo(
                id=strawberry.ID(datos["archivo_id"]),
                tipo=f"onboarding-{paso.value.lower()}",
                estado=EstadoArchivo.PENDIENTE,
                tipo_contenido=tipo_contenido,
                tamano_bytes=tamano_bytes,
                motivo=None,
                creado_en=datetime.now(UTC),
            ),
            url=datos["url"],
            metodo=datos["metodo"],
            encabezados=[
                EncabezadoSubida(nombre=k, valor=v) for k, v in datos["encabezados"].items()
            ],
            vence_en=datetime.fromisoformat(datos["vence_en"]),
        )

    @strawberry.mutation
    async def confirmar_subida_onboarding(
        self, info: Info, onboarding_id: strawberry.ID, archivo_id: strawberry.ID
    ) -> ResultadoOnboarding:
        async def llamada() -> dict:
            token = _token_onboarding(info)
            servicio = _onboarding(info)
            await servicio.confirmar_subida(str(onboarding_id), token, str(archivo_id))
            return await servicio.consultar(str(onboarding_id), token)

        return await _paso_onboarding(llamada)

    @strawberry.mutation
    async def enviar_codigo_onboarding(self, info: Info, correo: str) -> ResultadoCodigoOnboarding:
        """Envía un código de 6 dígitos al correo para verificarlo en el onboarding (HU-M01)."""
        try:
            datos = await info.context["servicio_codigo_onboarding"].enviar(correo)
        except ErrorOnboardingServicio as error:
            return _error_onboarding(error.codigo)
        except ErrorServicio:
            return _error_onboarding("NO_DISPONIBLE")
        return CodigoOnboardingEnviado(
            correo_enmascarado=str(datos["correo_enmascarado"]),
            vence_en=datos["vence_en"],
        )

    @strawberry.mutation
    async def verificar_codigo_onboarding(
        self, info: Info, correo: str, codigo: str
    ) -> ResultadoCodigoOnboarding:
        """Valida el código de 6 dígitos enviado al correo en el onboarding (HU-M01)."""
        try:
            datos = await info.context["servicio_codigo_onboarding"].verificar(correo, codigo)
        except ErrorOnboardingServicio as error:
            return _error_onboarding(error.codigo)
        except ErrorServicio:
            return _error_onboarding("NO_DISPONIBLE")
        return CodigoOnboardingEnviado(
            correo_enmascarado=str(datos["correo_enmascarado"]),
            vence_en=datos["vence_en"],
        )

    @strawberry.mutation
    async def registrar_cuenta(
        self, info: Info, correo: str, clave: str, codigo: str
    ) -> ResultadoSesion:
        """Crea la cuenta CLIENTE en PENDIENTE_VERIFICACION validando el código OTP del correo,
        y devuelve su sesión (scope `verificacion-pendiente`) para el canal móvil (HU-M01).
        Pública: no exige token previo ni sesión."""
        # T17: la clave se valida antes del OTP para no gastar el código con una clave débil.
        motivo = motivo_clave_invalida(clave)
        if motivo is not None:
            return ErrorAutenticacion(mensaje=motivo, codigo="CLAVE_DEBIL")
        try:
            await info.context["servicio_codigo_onboarding"].verificar(correo, codigo)
        except ErrorOnboardingServicio as error:
            mensaje = MENSAJES_ONBOARDING.get(error.codigo, "El código no es correcto o venció.")
            return ErrorAutenticacion(mensaje=mensaje, codigo=error.codigo)
        except ErrorServicio:
            return ErrorAutenticacion(
                mensaje="No pudimos verificar el código. Intenta de nuevo.",
                codigo="NO_DISPONIBLE",
            )

        try:
            cuenta = await _auth(info).crear_cuenta_onboarding(correo, clave)
            usuario_id = cuenta["id"]
        except ErrorOnboardingServicio as error:
            if error.codigo == "CORREO_YA_REGISTRADO":
                return ErrorAutenticacion(
                    mensaje="Ya existe una cuenta con ese correo.",
                    codigo="CORREO_YA_REGISTRADO",
                )
            return ErrorAutenticacion(
                mensaje="No se pudo registrar la cuenta.",
                codigo=error.codigo,
            )
        except (ErrorServicio, SolicitudInvalida, VerificacionPendiente):
            return ErrorAutenticacion(
                mensaje="No pudimos registrar tu cuenta. Intenta de nuevo.",
                codigo="NO_DISPONIBLE",
            )

        sesion = await _auth(info).iniciar_sesion(correo, clave, CANAL_APP)
        if sesion is None:
            return ErrorAutenticacion(
                mensaje="No se pudo iniciar la sesión.",
                codigo="NO_DISPONIBLE",
            )
        logger.info("cuenta registrada con otp usuario=%s", id_opaco(usuario_id))
        return _sesion(sesion)

    @strawberry.mutation
    async def crear_cuenta_onboarding(
        self, info: Info, onboarding_id: strawberry.ID, entrada: CuentaOnboardingInput
    ) -> ResultadoCuentaOnboarding:
        """M01-05b: cuenta CLIENTE en PENDIENTE_VERIFICACION ligada al recorrido, y su sesión
        (scope `verificacion-pendiente`). Idempotente si la cuenta ya se creó en un intento previo
        interrumpido con la misma clave. El correo y la clave no se loguean."""
        try:
            token = _token_onboarding(info)
            servicio = _onboarding(info)
            # Valida el token antes de crear nada en AUTH.
            actual = await servicio.consultar(str(onboarding_id), token)
            if actual.get("tiene_cuenta"):
                # Si la misma cuenta ya estaba vinculada a esta sesión, devolverla
                # de forma idempotente.
                try:
                    sesion = await _auth(info).iniciar_sesion(
                        entrada.correo, entrada.clave, CANAL_APP
                    )
                    if sesion is not None:
                        usuario_id = sesion["usuario"]["id"]
                        anterior = await servicio.de_usuario(usuario_id)
                        if anterior is not None and str(anterior["id"]) == str(onboarding_id):
                            return CuentaOnboarding(
                                sesion=_sesion(sesion),
                                onboarding=_onboarding_de(actual),
                            )
                except Exception:
                    pass
                # Si es otra cuenta diferente o las credenciales no corresponden:
                return _error_onboarding("SESION_CON_CUENTA")
            motivo = motivo_clave_invalida(entrada.clave)
            if motivo is not None:
                return ErrorOnboarding(codigo="CLAVE_DEBIL", mensaje=motivo)
            try:
                cuenta = await _auth(info).crear_cuenta_onboarding(entrada.correo, entrada.clave)
                sesion = await _auth(info).iniciar_sesion(entrada.correo, entrada.clave, CANAL_APP)
                if sesion is None:
                    return _error_onboarding("NO_DISPONIBLE")
                usuario_id = cuenta["id"]
            except ErrorOnboardingServicio as error:
                if error.codigo != "CORREO_YA_REGISTRADO":
                    raise
                # Si la cuenta quedó creada en PENDIENTE_VERIFICACION tras un corte de red previo,
                # iniciar sesión con la misma clave la recupera si sigue sin otra sesión.
                sesion = await _auth(info).iniciar_sesion(entrada.correo, entrada.clave, CANAL_APP)
                if sesion is None or sesion["usuario"]["estado"] != "PENDIENTE_VERIFICACION":
                    raise
                usuario_id = sesion["usuario"]["id"]
                anterior = await servicio.de_usuario(usuario_id)
                if anterior is not None and str(anterior["id"]) != str(onboarding_id):
                    raise
            ligada = await servicio.vincular_cuenta(str(onboarding_id), token, usuario_id)
        except ErrorOnboardingServicio as error:
            return _error_onboarding(error.codigo)
        except (ErrorServicio, SolicitudInvalida, VerificacionPendiente):
            return _error_onboarding("NO_DISPONIBLE")
        logger.info("cuenta de onboarding creada usuario=%s", id_opaco(usuario_id))
        return CuentaOnboarding(sesion=_sesion(sesion), onboarding=_onboarding_de(ligada))

    @strawberry.mutation
    async def enviar_verificacion(
        self, info: Info, onboarding_id: strawberry.ID
    ) -> ResultadoOnboarding:
        async def llamada() -> dict:
            return await _onboarding(info).enviar(str(onboarding_id), _token_onboarding(info))

        return await _paso_onboarding(llamada)

    @strawberry.mutation
    async def reintentar_onboarding(
        self, info: Info, onboarding_id: strawberry.ID
    ) -> ResultadoOnboarding:
        async def llamada() -> dict:
            return await _onboarding(info).reintentar(str(onboarding_id), _token_onboarding(info))

        return await _paso_onboarding(llamada)

    @strawberry.mutation
    async def actualizar_sesion(self, info: Info) -> ResultadoSesion:
        """Un token con los permisos del estado actual (p. ej. tras VERIFICADA) y la misma
        expiración del que llega: no alarga la sesión."""
        usuario = exigir_rol(info, *TODOS_LOS_ROLES, siempre=True, admite_pendiente=True)
        expira = datetime.fromtimestamp(usuario.expira or 0, UTC).isoformat()
        try:
            sesion = await _auth(info).renovar_sesion(usuario.id, expira)
        except (ErrorServicio, SolicitudInvalida) as error:
            raise ErrorPublico("No pudimos actualizar tu sesión. Intenta de nuevo.") from error
        if sesion is None:
            return ErrorAutenticacion(mensaje="Tu sesión ya no es válida. Inicia sesión de nuevo.")
        return _sesion(sesion)

    @strawberry.mutation
    async def aprobar_siniestro(self, info: Info, id: strawberry.ID) -> ResultadoResolucion:
        """HU-W04 (W04-6): CLAIMS registra primero la orden de pago en PAYMENTS y luego aprueba."""
        usuario = exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        return await _resolver_siniestro(lambda: _claims(info).aprobar(str(id), usuario.id))

    @strawberry.mutation
    async def rechazar_siniestro(
        self,
        info: Info,
        id: strawberry.ID,
        motivo: MotivoRechazo,
        comentario: str | None = None,
    ) -> ResultadoResolucion:
        usuario = exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        return await _resolver_siniestro(
            lambda: _claims(info).rechazar(str(id), motivo.value, comentario, usuario.id)
        )

    @strawberry.mutation
    async def derivar_siniestro(
        self,
        info: Info,
        id: strawberry.ID,
        perito_id: strawberry.ID,
        instrucciones: str | None = None,
    ) -> ResultadoResolucion:
        usuario = exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        return await _resolver_siniestro(
            lambda: _claims(info).derivar(str(id), str(perito_id), instrucciones, usuario.id)
        )


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        lambda: MaskErrors(should_mask_error=_mostrar_solo_errores_publicos),
        TrazaGraphQL,
        TraducirMensajes,
    ],
)
