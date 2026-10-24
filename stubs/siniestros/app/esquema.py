"""Esquema GraphQL del dummy de siniestros (HU-W04). Code-first con Strawberry, igual que el BFF.

El contrato equivalente en SDL vive en `contracts/graphql/siniestros.graphql` y la prueba
`tests/test_esquema_contrato.py` exige que ambos coincidan. Los nombres se exponen en camelCase
(Strawberry convierte los snake_case de Python).
"""

from datetime import datetime
from enum import Enum
from typing import Annotated

import strawberry
from strawberry.types import Info

from app.repositorio import ErrorNegocio, Repositorio, a_fecha


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


@strawberry.enum
class EstadoPoliza(Enum):
    EMITIDA = "EMITIDA"


@strawberry.type
class Monto:
    valor: str  # decimal como texto, nunca float
    moneda: str


@strawberry.type
class Asegurado:
    nombre: str
    documento: str


@strawberry.type
class Poliza:
    """Forma tomada de la tabla `polizas` y del tipo `Poliza` de HU-W03 (ver
    `app/datos/contrato-poliza.json`); `ramo` lo agrega el dummy."""

    id: strawberry.ID
    numero: str
    estado: EstadoPoliza
    vigencia_desde: datetime
    vigencia_hasta: datetime
    prima: Monto
    cobertura: Monto
    ramo: str


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
    url: str  # ruta relativa servida por este mismo servicio
    registrada_en: datetime
    geoetiquetada: bool
    nota: str | None


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
    vencimiento_informe: datetime | None  # solo en `peritos(siniestroId)`: si se le deriva hoy


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


@strawberry.type
class Siniestro:
    id: strawberry.ID
    radicado: str
    estado: EstadoSiniestro
    asegurado: Asegurado
    poliza: Poliza
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


@strawberry.type
class ErrorSiniestro:
    mensaje: str


ResultadoResolucion = Annotated[Siniestro | ErrorSiniestro, strawberry.union("ResultadoResolucion")]

PREFIJO_MEDIOS = "/graphql/siniestros/medios/"


def _monto(m: dict | None) -> Monto | None:
    return Monto(valor=str(m["valor"]), moneda=m["moneda"]) if m else None


def _perito(p: dict | None) -> Perito | None:
    if not p:
        return None
    return Perito(
        id=strawberry.ID(p["id"]),
        nombre=p["nombre"],
        ciudad=p["ciudad"],
        red=p["red"],
        registro=p["registro"],
        tiempo_respuesta_dias=p["tiempo_respuesta_dias"],
        vencimiento_informe=a_fecha(p.get("vencimiento_informe")),
    )


def _poliza(p: dict) -> Poliza:
    vigencia_desde = a_fecha(p["vigencia_desde"])
    return Poliza(
        id=strawberry.ID(p["id"]),
        # Formato de `numero_de_poliza` del servicio de pólizas: SOL-<año>-<6 dígitos>.
        numero=f"SOL-{vigencia_desde.year}-{p['numero']:06d}",
        estado=EstadoPoliza(p["estado"]),
        vigencia_desde=vigencia_desde,
        vigencia_hasta=a_fecha(p["vigencia_hasta"]),
        prima=_monto(p["prima"]),
        cobertura=_monto(p["cobertura"]),
        ramo=p["ramo"],
    )


def _resolucion(r: dict | None, repo: Repositorio) -> Resolucion | None:
    if not r:
        return None
    perito = repo.perito(r["perito_id"]) if r.get("perito_id") else None
    return Resolucion(
        tipo=TipoResolucion(r["tipo"]),
        resuelta_en=a_fecha(r["resuelta_en"]),
        notificacion=r["notificacion"],
        motivo=MotivoRechazo(r["motivo"]) if r.get("motivo") else None,
        comentario=r.get("comentario"),
        via_recurso=r.get("via_recurso"),
        indemnizacion=_monto(r.get("indemnizacion")),
        metodo_transferencia=r.get("metodo_transferencia"),
        plazo_dispersion=r.get("plazo_dispersion"),
        perito=_perito(perito),
        instrucciones=r.get("instrucciones"),
        vencimiento_informe=a_fecha(r.get("vencimiento_informe")),
    )


def a_siniestro(s: dict, repo: Repositorio) -> Siniestro:
    return Siniestro(
        id=strawberry.ID(s["id"]),
        radicado=s["radicado"],
        estado=EstadoSiniestro(s["estado"]),
        asegurado=Asegurado(**s["asegurado"]),
        poliza=_poliza(repo.poliza(s["poliza_id"])),
        cobertura_aplicada=s["cobertura_aplicada"],
        entidad_acreedora=s["entidad_acreedora"],
        ocurrido_en=a_fecha(s["ocurrido_en"]),
        notificado_en=a_fecha(s["notificado_en"]),
        ubicacion=Ubicacion(**s["ubicacion"]),
        descripcion=s["descripcion"],
        suma_reclamada=_monto(s["suma_reclamada"]),
        vence_dictamen_en=a_fecha(s["vence_dictamen_en"]),
        evidencias=[
            Evidencia(
                id=strawberry.ID(e["id"]),
                tipo=TipoEvidencia(e["tipo"]),
                titulo=e["titulo"],
                archivo=e["archivo"],
                url=PREFIJO_MEDIOS + e["archivo"],
                registrada_en=a_fecha(e["registrada_en"]),
                geoetiquetada=e["geoetiquetada"],
                nota=e.get("nota"),
            )
            for e in s["evidencias"]
        ],
        verificaciones=[Verificacion(**v) for v in s["verificaciones"]],
        resolucion=_resolucion(s.get("resolucion"), repo),
    )


def _repo(info: Info) -> Repositorio:
    return info.context["repo"]


def _resultado(info: Info, accion) -> ResultadoResolucion:
    try:
        return a_siniestro(accion(_repo(info)), _repo(info))
    except ErrorNegocio as error:
        return ErrorSiniestro(mensaje=str(error))


@strawberry.type
class Query:
    @strawberry.field
    def siniestros(self, info: Info, estado: EstadoSiniestro | None = None) -> list[Siniestro]:
        repo = _repo(info)
        return [a_siniestro(s, repo) for s in repo.listar(estado.value if estado else None)]

    @strawberry.field
    def siniestro(self, info: Info, id: strawberry.ID) -> Siniestro | None:
        repo = _repo(info)
        s = repo.obtener(str(id))
        return a_siniestro(s, repo) if s else None

    @strawberry.field
    def peritos(self, info: Info, siniestro_id: strawberry.ID) -> list[Perito]:
        return [_perito(p) for p in _repo(info).peritos(str(siniestro_id))]


@strawberry.type
class Mutation:
    @strawberry.mutation
    def aprobar_siniestro(self, info: Info, id: strawberry.ID) -> ResultadoResolucion:
        return _resultado(info, lambda repo: repo.aprobar(str(id)))

    @strawberry.mutation
    def rechazar_siniestro(
        self,
        info: Info,
        id: strawberry.ID,
        motivo: MotivoRechazo,
        comentario: str | None = None,
    ) -> ResultadoResolucion:
        return _resultado(info, lambda repo: repo.rechazar(str(id), motivo.value, comentario))

    @strawberry.mutation
    def derivar_siniestro(
        self,
        info: Info,
        id: strawberry.ID,
        perito_id: strawberry.ID,
        instrucciones: str | None = None,
    ) -> ResultadoResolucion:
        return _resultado(info, lambda repo: repo.derivar(str(id), str(perito_id), instrucciones))


schema = strawberry.Schema(query=Query, mutation=Mutation)
