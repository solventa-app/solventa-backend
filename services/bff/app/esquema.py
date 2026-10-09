"""Esquema GraphQL del BFF: espejo 1:1 de `contracts/graphql/schema.graphql`.

El contrato manda (regla #8). `tests/test_esquema_contrato.py` compara este
esquema con el contrato en ambos sentidos: si difieren, se corrige el código,
no el contrato.

Operaciones sin backend todavía (alcance del Sprint 1, KAN-24):
- `oferta(id)` devuelve `null`: RATING no persiste ofertas aún.
- `contactarAsesor` es un MOCK que solo confirma.
- `cobros`/`reintentarCobro` responden un error "no disponible" hasta KAN-28.
Ninguna produce un 5xx: los errores de GraphQL viajan con HTTP 200."""

from datetime import datetime
from enum import Enum
from typing import Annotated

import strawberry
from graphql import GraphQLError
from strawberry.extensions import MaskErrors
from strawberry.types import Info

from app.clientes import ErrorServicio
from app.servicio_oferta import ErrorOfertaDatos


class ErrorPublico(Exception):
    """Error cuyo mensaje SÍ puede ver el cliente. Cualquier otra excepción se
    enmascara como "Unexpected error." (sin detalles internos)."""


def _mostrar_solo_errores_publicos(error: GraphQLError) -> bool:
    return not isinstance(error.original_error, ErrorPublico)


# ---------- Tipos ----------


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
    vigente: bool


@strawberry.input
class ConsentimientoInput:
    finalidad: str
    version_texto: str


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


@strawberry.type
class ErrorOferta:
    mensaje: str
    puede_reintentar: bool


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
class Cobro:
    id: strawberry.ID
    poliza_id: strawberry.ID
    estado: EstadoCobro
    monto: Monto
    actualizado_en: datetime


# ---------- Traducción desde los servicios ----------


def _fecha(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


def _consentimiento(datos: dict) -> Consentimiento:
    return Consentimiento(
        id=strawberry.ID(datos["id"]),
        finalidad=datos["finalidad"],
        version_texto=datos["version_texto"],
        otorgado_en=datetime.fromisoformat(datos["otorgado_en"]),
        revocado_en=_fecha(datos.get("revocado_en")),
        vigente=datos["vigente"],
    )


def _monto(datos: dict | None) -> Monto | None:
    return Monto(valor=datos["valor"], moneda=datos["moneda"]) if datos else None


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
    )


# ---------- Operaciones ----------


@strawberry.type
class Query:
    @strawberry.field
    def oferta(self, id: strawberry.ID) -> Oferta | None:
        # RATING no persiste ofertas todavía: no hay de dónde leerla.
        return None

    @strawberry.field
    def cobros(self, estado: EstadoCobro | None = strawberry.UNSET) -> list[Cobro]:
        raise ErrorPublico("no disponible: KAN-28")


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def registrar_consentimiento(
        self, info: Info, entrada: ConsentimientoInput
    ) -> Consentimiento:
        try:
            datos = await info.context["clientes"].auth.registrar_consentimiento(
                entrada.finalidad, entrada.version_texto
            )
        except ErrorServicio as error:
            raise ErrorPublico(
                "No pudimos registrar el consentimiento. Intenta de nuevo."
            ) from error
        return _consentimiento(datos)

    @strawberry.mutation
    async def revocar_consentimiento(self, info: Info, id: strawberry.ID) -> Consentimiento:
        try:
            datos = await info.context["clientes"].auth.revocar_consentimiento(str(id))
        except ErrorServicio as error:
            raise ErrorPublico("No pudimos revocar el consentimiento. Intenta de nuevo.") from error
        if datos is None:
            raise ErrorPublico("Consentimiento no encontrado.")
        return _consentimiento(datos)

    @strawberry.mutation
    async def solicitar_oferta(self, info: Info, entrada: SolicitudOfertaInput) -> ResultadoOferta:
        resultado = await info.context["servicio_oferta"].solicitar(
            str(entrada.consentimiento_id), entrada.documento_identidad, entrada.valor_credito
        )
        if isinstance(resultado, ErrorOfertaDatos):
            return ErrorOferta(
                mensaje=resultado.mensaje, puede_reintentar=resultado.puede_reintentar
            )
        return _oferta(resultado)

    @strawberry.mutation
    def contactar_asesor(
        self, oferta_id: strawberry.ID, canal: CanalContacto
    ) -> ConfirmacionContacto:
        # MOCK (CA-W01-07): no hay servicio de asesores todavía; solo confirma.
        return ConfirmacionContacto(
            canal=canal,
            resumen=f"Recibimos tu solicitud ({canal.value}) sobre la oferta {oferta_id}. "
            "Un asesor te contactará.",
        )

    @strawberry.mutation
    def reintentar_cobro(self, cobro_id: strawberry.ID) -> Cobro:
        raise ErrorPublico("no disponible: KAN-28")


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[lambda: MaskErrors(should_mask_error=_mostrar_solo_errores_publicos)],
)
