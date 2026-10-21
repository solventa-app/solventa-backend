"""Esquema GraphQL del BFF: espejo 1:1 de `contracts/graphql/schema.graphql`.

El contrato manda (regla #8). `tests/test_esquema_contrato.py` compara este
esquema con el contrato en ambos sentidos: si difieren, se corrige el código,
no el contrato.

Operaciones sin backend todavía (alcance del Sprint 1, KAN-24):
- `oferta(id)` devuelve `null`: RATING no persiste ofertas aún.
- `contactarAsesor` es un MOCK que solo confirma.
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
from app.clientes import ErrorServicio, SolicitudInvalida
from app.identificadores import cliente_id_desde_documento, id_opaco
from app.idioma import TraducirMensajes
from app.servicio_oferta import ErrorOfertaDatos
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
    documento_identidad: str
    valor_credito: str
    plazo_meses: int
    direccion_inmueble: str


@strawberry.type
class ConfirmacionContacto:
    canal: CanalContacto
    resumen: str


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
    )


def _usuario(datos: dict) -> Usuario:
    return Usuario(
        id=strawberry.ID(datos["id"]),
        usuario=datos["usuario"],
        rol=RolUsuario(datos["rol"]),
        estado=EstadoUsuario(datos["estado"]),
        creado_en=datetime.fromisoformat(datos["creado_en"]),
        nombre=datos.get("nombre"),
    )


# ---------- Operaciones ----------


CLIENTE_O_ASESOR = ("CLIENTE", "ASESOR")
SOLO_BACKOFFICE = ("BACKOFFICE",)
MENSAJE_AUTH_NO_DISPONIBLE = "No pudimos procesar la solicitud. Intenta de nuevo."
MENSAJE_USUARIO_NO_ENCONTRADO = "Usuario no encontrado."
MENSAJE_COBROS_NO_DISPONIBLES = "No pudimos consultar los cobros. Intenta de nuevo."


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
    def oferta(self, info: Info, id: strawberry.ID) -> Oferta | None:
        exigir_rol(info, *CLIENTE_O_ASESOR)
        # RATING no persiste ofertas todavía: no hay de dónde leerla.
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
                entrada.usuario, entrada.clave, entrada.rol.value, entrada.nombre
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
    async def suspender_usuario(self, info: Info, id: strawberry.ID) -> ResultadoUsuario:
        exigir_rol(info, *SOLO_BACKOFFICE, siempre=True)
        try:
            datos = await _auth(info).suspender_usuario(str(id))
        except ErrorServicio as error:
            raise ErrorPublico(MENSAJE_AUTH_NO_DISPONIBLE) from error
        if datos is None:
            return ErrorUsuario(mensaje=MENSAJE_USUARIO_NO_ENCONTRADO)
        return _usuario(datos)


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        lambda: MaskErrors(should_mask_error=_mostrar_solo_errores_publicos),
        TrazaGraphQL,
        TraducirMensajes,
    ],
)
