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
- HU-W03 bloque 9 (contrato v0.18): las operaciones del asesor, de la página del enlace,
  `propuestas` y `ticketAvisos` son solo el esqueleto: responden «aún no disponible» con
  reintento hasta F-B6 (`docs/plan-hu-w03-frans.md`). `aceptarOferta` recibe el contacto pero
  aún no lo guarda.
`cobros` y `reintentarCobro` (KAN-28) leen y reintentan en PAYMENTS, que concilia
con la pasarela antes de cobrar (ver `docs/cobros-conciliacion.md`).
Ninguna produce un 5xx: los errores de GraphQL viajan con HTTP 200.

Permisos (HU-M02): cada resolutor llama a `exigir_rol` con los roles que admite. La
matriz completa está en `docs/autenticacion-usuarios.md`."""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated

import strawberry
from graphql import GraphQLError
from strawberry.extensions import MaskErrors
from strawberry.types import Info

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO, MENSAJE_SIN_SESION, UsuarioActual
from app.clientes import ArchivoRechazado, ErrorServicio, SolicitudInvalida
from app.identificadores import cliente_id_desde_documento, id_opaco
from app.idioma import TraducirMensajes
from app.monedas import (
    MAXIMO_MONTOS,
    MENSAJE_DEMASIADOS_MONTOS,
    MENSAJE_TASAS_NO_DISPONIBLES,
    ConversionInvalida,
    convertir,
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


def exigir_rol(info: Info, *roles: str, siempre: bool = False) -> UsuarioActual | None:
    """Aplica la autorización de una operación y devuelve el usuario (o `None`).

    - Token presente pero inválido o vencido: error, en cualquier modo.
    - Sin token: error si `siempre` o si la bandera está en `obligatoria`; si no
      (modo `opcional`), se permite como antes y devuelve `None`.
    - Con token: el rol debe estar en `roles`."""
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


@strawberry.type
class Sesion:
    token: str
    expira_en: datetime
    usuario: Usuario


@strawberry.type
class ErrorAutenticacion:
    mensaje: str


ResultadoSesion = Annotated[Sesion | ErrorAutenticacion, strawberry.union("ResultadoSesion")]


@strawberry.type
class ErrorUsuario:
    mensaje: str


ResultadoUsuario = Annotated[Usuario | ErrorUsuario, strawberry.union("ResultadoUsuario")]


@strawberry.input
class CredencialesInput:
    usuario: str
    clave: str


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


def _propuesta(datos: dict) -> Propuesta:
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
    `nombres` es `{cliente_id: nombre}` de AUTH; sin él, el cliente sale sin nombre."""
    cliente_id = datos.get("cliente_id")
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
            ClienteCobro(id=strawberry.ID(cliente_id), nombre=(nombres or {}).get(cliente_id))
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


# ---------- Operaciones ----------


CLIENTE_O_ASESOR = ("CLIENTE", "ASESOR")
SOLO_BACKOFFICE = ("BACKOFFICE",)
SOLO_CLIENTE = ("CLIENTE",)
MENSAJE_AUTH_NO_DISPONIBLE = "No pudimos procesar la solicitud. Intenta de nuevo."
MENSAJE_USUARIO_NO_ENCONTRADO = "Usuario no encontrado."
MENSAJE_COBROS_NO_DISPONIBLES = "No pudimos consultar los cobros. Intenta de nuevo."
# Esqueleto de la v0.18 (HU-W03, bloque 9): se reemplaza al construir cada operación (F-B6).
MENSAJE_OPERACION_NO_DISPONIBLE = "Esta operación aún no está disponible. Intenta más tarde."
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
    return nombres


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
        """W03-35: filtradas por el rol del token. Esqueleto de la v0.18 hasta F-B6."""
        exigir_rol(info, *TODOS_LOS_ROLES, siempre=True)
        raise ErrorPublico(MENSAJE_OPERACION_NO_DISPONIBLE)

    @strawberry.field
    async def firma_por_enlace(
        self, info: Info, propuesta_id: strawberry.ID | None = None
    ) -> EnlaceFirma | None:
        """W03-33: token del enlace o sesión de CLIENTE. Esqueleto de la v0.18 hasta F-B6."""
        return None

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
    async def yo(self, info: Info) -> Usuario | None:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _auth(info).obtener_usuario(usuario.id)
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        return _usuario(datos) if datos else None

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
        titular_id = (
            cliente_id_desde_documento(entrada.documento_identidad, clave_titular)
            if entrada.documento_identidad
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
            try:
                await info.context["clientes"].acl.invalidar_cache_cliente(titular_id)
            except (ErrorServicio, AttributeError):
                logger.warning("no se pudo invalidar cache del titular al revocar consentimiento")
        return _consentimiento(datos)

    @strawberry.mutation
    async def solicitar_oferta(self, info: Info, entrada: SolicitudOfertaInput) -> ResultadoOferta:
        exigir_rol(info, *CLIENTE_O_ASESOR)
        if not entrada.documento_identidad:
            # Hasta F-B6 el BFF no lo toma de AUTH para el CLIENTE: sigue siendo obligatorio.
            return ErrorOferta(
                mensaje=MENSAJE_DOCUMENTO_REQUERIDO,
                puede_reintentar=False,
                motivo=MotivoErrorOferta.DATOS_INSUFICIENTES,
            )
        resultado = await info.context["servicio_oferta"].solicitar(
            str(entrada.consentimiento_id),
            entrada.documento_identidad,
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
        contacto: ContactoTitularInput | None = None,  # v0.18: se guarda desde F-B5/F-B6
    ) -> ResultadoAceptacion:
        usuario = exigir_rol(info, *CLIENTE_O_ASESOR)
        resultado = await ServicioSuscripcion(info.context["clientes"]).aceptar(
            str(oferta_id), usuario
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
        try:
            sesion = await _auth(info).iniciar_sesion(entrada.usuario, entrada.clave)
        except ErrorServicio as error:
            raise ErrorPublico("No pudimos iniciar sesión. Intenta de nuevo.") from error
        if sesion is None:
            return ErrorAutenticacion(mensaje="Usuario o clave incorrectos.")
        return Sesion(
            token=sesion["token"],
            expira_en=datetime.fromisoformat(sesion["expira_en"]),
            usuario=_usuario(sesion["usuario"]),
        )

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

    # ---------- v0.18 (HU-W03, bloque 9): esqueleto hasta F-B6 ----------

    @strawberry.mutation
    async def enviar_codigo_verificacion(
        self, info: Info, propuesta_id: strawberry.ID, correo: str | None = None
    ) -> ResultadoEnvioCodigo:
        exigir_rol(info, "ASESOR", siempre=True)
        return ErrorCodigo(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def verificar_codigo(
        self, info: Info, propuesta_id: strawberry.ID, codigo: str
    ) -> ResultadoVerificacion:
        exigir_rol(info, "ASESOR", siempre=True)
        return ErrorCodigo(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def emitir_propuesta(self, info: Info, propuesta_id: strawberry.ID) -> ResultadoEmision:
        exigir_rol(info, *CLIENTE_O_ASESOR, siempre=True)
        return ErrorEmision(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def reenviar_enlace_firma(
        self, info: Info, propuesta_id: strawberry.ID
    ) -> ResultadoEmision:
        exigir_rol(info, "ASESOR", siempre=True)
        return ErrorEmision(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def solicitar_subida_firma(
        self,
        info: Info,
        tipo_contenido: str,
        tamano_bytes: int,
        propuesta_id: strawberry.ID | None = None,
    ) -> ResultadoSubidaArchivo:
        return ErrorArchivo(codigo="NO_DISPONIBLE", mensaje=MENSAJE_OPERACION_NO_DISPONIBLE)

    @strawberry.mutation
    async def firmar_por_enlace(self, info: Info, entrada: FirmarPorEnlaceInput) -> ResultadoFirma:
        return ErrorFirma(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def iniciar_pago(
        self, info: Info, propuesta_id: strawberry.ID | None = None
    ) -> ResultadoInicioPago:
        return ErrorPago(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def confirmar_pago(
        self, info: Info, sesion_id: strawberry.ID, propuesta_id: strawberry.ID | None = None
    ) -> ResultadoPago:
        return ErrorPago(mensaje=MENSAJE_OPERACION_NO_DISPONIBLE, puede_reintentar=True)

    @strawberry.mutation
    async def ticket_avisos(self, info: Info) -> TicketAvisos:
        exigir_rol(info, *TODOS_LOS_ROLES, siempre=True)
        raise ErrorPublico(MENSAJE_OPERACION_NO_DISPONIBLE)


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        lambda: MaskErrors(should_mask_error=_mostrar_solo_errores_publicos),
        TrazaGraphQL,
        TraducirMensajes,
    ],
)
