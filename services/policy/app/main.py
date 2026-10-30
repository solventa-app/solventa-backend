"""POLICY: propuestas y pólizas (HU-W03).

Único escritor de las tablas de suscripción (regla de arquitectura #1). Lee la oferta de RATING y
nunca llama a un proveedor externo directamente (regla #2): la firma, el KYC y las notificaciones
pasan por el ACL Worker en los bloques siguientes.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from app.application.autenticacion_servicios import autenticacion_para
from app.application.avisos import AvisosNulos, AvisosRedis, estado_visible
from app.application.bus_eventos import BusEventosLog
from app.application.cifrado import cifrador_desde_entorno, clave_desde_entorno
from app.application.cliente_acl import ClienteAcl
from app.application.cliente_almacenamiento import ClienteAlmacenamiento, reglas_de_reescritura
from app.application.cliente_auth import ClienteAuth
from app.application.cliente_payments import ClientePayments
from app.application.cliente_rating import ClienteRating, RatingNoDisponible
from app.application.cliente_under import ClienteUnder
from app.application.configuracion import Configuracion
from app.application.huellas import clave_de_huellas
from app.application.planificador import (
    EmisionPendiente,
    PlanificadorCloudTasks,
    PlanificadorLocal,
    PuertoPlanificador,
)
from app.application.repositorio_propuestas import RepositorioPropuestasPostgres, construir_pool
from app.application.servicio_emision import (
    PropuestaNoEncontradaParaEmitir,
    ServicioEmision,
)
from app.application.servicio_firma import (
    COBRO_NO_AUTORIZADO,
    PROPUESTA_NO_FIRMABLE,
    SERVICIO_FIRMA_NO_DISPONIBLE,
    FirmaNoPermitida,
    PropuestaNoEncontrada,
    ServicioFirma,
)
from app.application.servicio_firma_y_pago import (
    Autorizacion,
    ErrorFlujo,
    Reglas,
    ServicioFirmaYPago,
)
from app.application.servicio_polizas import DocumentoNoDisponible, ServicioPolizas
from app.application.servicio_propuestas import (
    OFERTA_NO_ENCONTRADA,
    OfertaNoAceptable,
    ServicioPropuestas,
    TasaInvalida,
)
from app.application.servicio_purga import ServicioPurga
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx registra cada URL pedida, y las URLs firmadas del almacenamiento no se registran (regla 6).
logging.getLogger("httpx").setLevel(logging.WARNING)

configurar_trazas("policy", instrumentos=("httpx", "psycopg"))


class ContactoTitularRequest(BaseModel):
    """El contacto que da el ASESOR (W03-25): se guarda cifrado y nunca se loguea."""

    nombre: str = Field(min_length=2, max_length=100)
    correo: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AceptarOfertaRequest(BaseModel):
    oferta_id: str = Field(min_length=1)
    # El usuario (CLIENTE o ASESOR) que aceptó; solo su id opaco, para el rastro de quién hizo qué.
    actor_usuario_id: str | None = Field(default=None, min_length=1)
    # Bloque 9: el rol de quien aceptó y, si es ASESOR o SOCIO, el contacto del titular (W03-25).
    actor_rol: str | None = Field(default=None, pattern="^(CLIENTE|ASESOR|SOCIO)$")
    contacto: ContactoTitularRequest | None = None
    # HU-W07: la tasa del socio, congelada en la propuesta (W07-7). Solo con `actor_rol = SOCIO`.
    tasa_comision: str | None = Field(default=None, pattern=r"^0(\.\d{1,4})?$")


class MontoResponse(BaseModel):
    valor: str
    moneda: str


class PropuestaResponse(BaseModel):
    id: str
    oferta_id: str
    cliente_id: str
    # Quién aceptó: el BFF deja que un CLIENTE vea solo sus propuestas.
    actor_usuario_id: str | None = None
    estado: str
    prima: MontoResponse
    cobertura: MontoResponse
    plazo_meses: int | None = None
    poliza_id: str | None = None
    # Por qué quedó en revisión o no se emitió (códigos, sin datos personales).
    motivo_estado: str | None = None
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None
    # Bloque 9.
    actor_rol: str | None = None
    origen_contacto: str | None = None
    estado_visible: str | None = None


class PolizaResumenResponse(BaseModel):
    id: str
    numero: str
    vigencia_desde: datetime
    vigencia_hasta: datetime


class PropuestaListadaResponse(PropuestaResponse):
    # Dato personal (el nombre que dio el ASESOR): el BFF decide si lo muestra según el rol.
    nombre_titular: str | None = None
    poliza: PolizaResumenResponse | None = None


class PaginaPropuestasResponse(BaseModel):
    propuestas: list[PropuestaListadaResponse]
    total: int
    pagina: int
    tamano: int


class VentasMesResponse(BaseModel):
    mes: str  # AAAA-MM
    propuestas: int
    emitidas: int
    primas: MontoResponse
    comision: MontoResponse


class VentasTotalesResponse(BaseModel):
    propuestas: int
    emitidas: int
    primas: MontoResponse
    comision: MontoResponse


class VentasSocioResponse(BaseModel):
    """HU-W07: lo que vendió un socio, por mes. Sin datos de los titulares."""

    desde: date
    hasta: date
    meses: list[VentasMesResponse]
    totales: VentasTotalesResponse


class CodigoRequest(BaseModel):
    correo: str | None = Field(default=None, max_length=254)


class CodigoResponse(BaseModel):
    propuesta_id: str
    correo_enmascarado: str
    vence_en: datetime


class VerificarCodigoRequest(BaseModel):
    codigo: str = Field(min_length=1, max_length=12)


class EmitirRequest(BaseModel):
    rol: str = Field(pattern="^(CLIENTE|ASESOR|SOCIO)$")
    titular_id: str | None = Field(default=None, min_length=1)


class EnlaceResponse(BaseModel):
    correo_enmascarado: str
    vence_en: datetime  # la URL nunca vuelve: solo va al correo del titular


class EmisionPropuestaResponse(BaseModel):
    propuesta: PropuestaResponse
    enlace: EnlaceResponse | None = None


class ResolverEnlaceRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)


class ResolverEnlaceResponse(BaseModel):
    propuesta_id: str
    vigente: bool
    vence_en: datetime


class EvidenciaResponse(BaseModel):
    referencia: str
    firmada_en: datetime
    modo_firma: str | None = None


class EstadoEnlaceResponse(BaseModel):
    propuesta: PropuestaResponse
    vigente: bool
    vence_en: datetime | None = None
    firma: EvidenciaResponse | None = None


class SubidaFirmaRequest(BaseModel):
    tipo_contenido: str = Field(min_length=1, max_length=100)
    tamano_bytes: int = Field(gt=0)


class SubidaFirmaResponse(BaseModel):
    archivo: dict
    url: str
    metodo: str
    encabezados: dict[str, str]
    vence_en: datetime


class FirmarEnlaceRequest(BaseModel):
    version_documento: str = Field(min_length=1)
    acepta_terminos: bool
    autoriza_cobro: bool
    modo: str = Field(pattern="^(TRAZO|IMAGEN|NOMBRE)$")
    imagen_archivo_id: str | None = Field(default=None, min_length=1)
    nombre_firma: str | None = Field(default=None, max_length=200)


class PagoRequest(BaseModel):
    url_retorno: str = Field(min_length=1, max_length=500)
    idioma: str = "es"


class SesionPagoResponse(BaseModel):
    sesion_id: str | None = None
    estado: str
    url_pago: str | None = None
    vence_en: datetime | None = None
    motivo: str | None = None
    fallos_seguidos: int = 0


class PagoResponse(BaseModel):
    sesion: SesionPagoResponse | None = None
    poliza: PolizaResumenResponse | None = None


class RevocacionRequest(BaseModel):
    titular_id: str = Field(min_length=1)
    consentimiento_id: str = Field(min_length=1)


class ResumenPurgaResponse(BaseModel):
    revisadas: int
    purgadas: int
    sin_respuesta: int


class DocumentoFirmaResponse(BaseModel):
    propuesta_id: str
    version: str
    resumen: str
    monto_mensual: MontoResponse
    meses: int
    forma_cobro: str


class FirmarRequest(BaseModel):
    autoriza_cobro: bool
    version_documento: str = Field(min_length=1)
    # Solo el id opaco del usuario que firma, nunca un documento ni un correo.
    firmante_id: str = Field(min_length=1)


class FirmaResponse(BaseModel):
    propuesta_id: str
    firmada_en: datetime
    referencia: str  # la referencia de la evidencia de no repudio
    estado: str  # el estado de la propuesta después de firmar


class EmisionResponse(BaseModel):
    estado: str


class ResumenReconciliacionResponse(BaseModel):
    revisadas: int
    emitidas: int
    rechazadas: int
    pendientes: int
    fallidas: int


class PolizaResponse(BaseModel):
    id: str
    numero: str
    propuesta_id: str
    cliente_id: str
    estado: str
    vigencia_desde: datetime
    vigencia_hasta: datetime
    prima: MontoResponse
    cobertura: MontoResponse
    # Quién aceptó: el BFF deja que un CLIENTE vea solo sus pólizas.
    actor_usuario_id: str | None = None
    # Quien firmó (el CLIENTE titular): de él es el nombre del documento y a él se le avisa.
    firmante_id: str | None = None
    # Cuándo se avisó al cliente; nulo mientras el aviso siga pendiente.
    notificada_en: datetime | None = None


class DocumentoRequest(BaseModel):
    # Va en el CUERPO, nunca en la URL (las URL quedan en los logs de acceso, regla #6).
    asegurado: str | None = Field(default=None, max_length=200)


class ErrorResponse(BaseModel):
    detail: str


def _respuesta(propuesta: dict[str, Any]) -> PropuestaResponse:
    return PropuestaResponse(**propuesta, estado_visible=estado_visible(propuesta["estado"]))


def _poliza_resumen(poliza: dict[str, Any] | None) -> PolizaResumenResponse | None:
    if not poliza:
        return None
    from app.application.servicio_emision import numero_de_poliza

    numero = poliza.get("numero_formateado") or numero_de_poliza(
        poliza["numero"], poliza["vigencia_desde"]
    )
    return PolizaResumenResponse(
        id=poliza["id"],
        numero=numero,
        vigencia_desde=poliza["vigencia_desde"],
        vigencia_hasta=poliza["vigencia_hasta"],
    )


def _pago(resultado: dict[str, Any]) -> PagoResponse:
    sesion = resultado.get("sesion")
    return PagoResponse(
        sesion=SesionPagoResponse(
            sesion_id=sesion.sesion_id,
            estado=sesion.estado,
            url_pago=sesion.url_pago,
            vence_en=sesion.vence_en,
            motivo=sesion.motivo,
            fallos_seguidos=sesion.fallos_seguidos,
        )
        if sesion
        else None,
        poliza=_poliza_resumen(resultado.get("poliza")),
    )


def _planificador(config: Configuracion) -> PuertoPlanificador:
    if config.disparador != "externo":
        return PlanificadorLocal()
    return PlanificadorCloudTasks(
        config.tareas_proyecto,
        config.tareas_region,
        config.tareas_cola,
        config.url_propia,
        config.tareas_cuenta,
    )


def create_app(
    config: Configuracion | None = None,
    servicio_propuestas: ServicioPropuestas | None = None,
    servicio_firma: ServicioFirma | None = None,
    servicio_emision: ServicioEmision | None = None,
    servicio_polizas: ServicioPolizas | None = None,
    servicio_firma_y_pago: ServicioFirmaYPago | None = None,
    servicio_purga: ServicioPurga | None = None,
) -> FastAPI:
    """Los `servicio_*` permiten inyectar dobles de prueba sin PostgreSQL ni
    RATING reales (ver `tests/fakes.py`). En producción se omiten y `create_app` los construye
    desde `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: ConnectionPool | None = None
        cliente_rating: ClienteRating | None = None
        cliente_acl: ClienteAcl | None = None
        cliente_under: ClienteUnder | None = None
        cliente_payments: ClientePayments | None = None
        cliente_auth: ClienteAuth | None = None
        cliente_almacen: ClienteAlmacenamiento | None = None
        if servicio_propuestas is not None:
            app.state.servicio_propuestas = servicio_propuestas
            app.state.servicio_firma = servicio_firma
            app.state.servicio_emision = servicio_emision
            app.state.servicio_polizas = servicio_polizas
            app.state.servicio_firma_y_pago = servicio_firma_y_pago
            app.state.servicio_purga = servicio_purga
        else:
            pool = construir_pool(config.database_url)
            cliente_rating = ClienteRating(
                config.rating_url,
                config.rating_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "rating"),
            )
            repositorio = RepositorioPropuestasPostgres(pool, cifrador_desde_entorno())
            cliente_acl = ClienteAcl(
                config.acl_url,
                config.acl_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "acl-worker"),
            )
            cliente_under = ClienteUnder(
                config.under_url,
                config.under_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "under"),
            )
            cliente_payments = ClientePayments(
                config.payments_url,
                config.payments_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "payments"),
            )
            cliente_auth = ClienteAuth(
                config.auth_url,
                config.auth_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "auth"),
            )
            cliente_almacen = ClienteAlmacenamiento(
                config.almacenamiento_url,
                config.almacenamiento_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "almacenamiento"),
                reglas_de_reescritura(config.almacenamiento_reescritura),
            )
            avisos = AvisosRedis(config.redis_url) if config.redis_url else AvisosNulos()
            planificador = _planificador(config)
            servicio_de_polizas = ServicioPolizas(
                repositorio, config.documento_ambiente_pruebas, cliente_almacen
            )
            emision = ServicioEmision(
                repositorio,
                cliente_under,
                cliente_payments,
                BusEventosLog(),
                planificador,
                cliente_acl,
                config.token_recaudo_credito,
                config.cobro_pendiente_demora_segundos,
                config.emision_max_intentos,
                config.reconciliacion_lote_max,
                config.reconciliacion_dias,
                contactos=cliente_auth,
                generador_documento=servicio_de_polizas.documento,
                avisos=avisos,
            )
            if isinstance(planificador, PlanificadorLocal):
                planificador.conectar(emision.emitir)
            app.state.servicio_propuestas = ServicioPropuestas(repositorio, cliente_rating)
            app.state.servicio_firma = ServicioFirma(repositorio, cliente_acl, planificador)
            app.state.servicio_emision = emision
            app.state.servicio_polizas = servicio_de_polizas
            app.state.servicio_firma_y_pago = ServicioFirmaYPago(
                repositorio,
                cliente_under,
                cliente_acl,
                cliente_acl,
                cliente_almacen,
                cliente_payments,
                emision.emitir_pagada,
                Reglas(
                    clave_huellas=clave_de_huellas(clave_desde_entorno()),
                    url_front=config.url_front,
                    codigo_vigencia=timedelta(minutes=config.codigo_vigencia_minutos),
                    codigo_max_intentos=config.codigo_max_intentos,
                    enlace_vigencia=timedelta(minutes=config.enlace_vigencia_minutos),
                ),
                avisos,
            )
            app.state.servicio_purga = ServicioPurga(
                repositorio, cliente_auth, cliente_almacen, avisos, config.purga_lote_max
            )
        try:
            yield
        finally:
            for cliente in (cliente_under, cliente_payments, cliente_auth, cliente_almacen):
                if cliente is not None:
                    cliente.cerrar()
            if cliente_acl is not None:
                cliente_acl.cerrar()
            if cliente_rating is not None:
                cliente_rating.cerrar()
            if pool is not None:
                pool.close()

    app = FastAPI(title="solventa-policy", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ErrorFlujo)
    async def _error_flujo(_: Request, error: ErrorFlujo) -> JSONResponse:
        return JSONResponse(
            status_code=error.estado_http, content={"detail": error.motivo, **error.detalle}
        )

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "policy", "estado": "ok"}

    @app.post(
        "/propuestas",
        tags=["propuestas"],
        response_model=PropuestaResponse,
        responses={
            404: {"model": ErrorResponse, "description": "la oferta venció o no existe"},
            422: {"model": ErrorResponse, "description": "OFERTA_PRELIMINAR u OFERTA_VENCIDA"},
            503: {"model": ErrorResponse, "description": "RATING no responde; se puede reintentar"},
        },
    )
    def aceptar_oferta(body: AceptarOfertaRequest) -> PropuestaResponse:
        """CA-W03-01: acepta una oferta COMPLETA y vigente. Idempotente por `oferta_id`."""
        try:
            propuesta = app.state.servicio_propuestas.aceptar(
                body.oferta_id,
                body.actor_usuario_id,
                body.actor_rol,
                body.contacto.model_dump() if body.contacto else None,
                body.tasa_comision,
            )
        except TasaInvalida as error:
            raise HTTPException(status_code=422, detail="TASA_COMISION_INVALIDA") from error
        except OfertaNoAceptable as error:
            codigo = 404 if error.motivo == OFERTA_NO_ENCONTRADA else 422
            raise HTTPException(status_code=codigo, detail=error.motivo) from error
        except RatingNoDisponible as error:
            raise HTTPException(status_code=503, detail="RATING_NO_DISPONIBLE") from error
        return _respuesta(propuesta)

    @app.get(
        "/propuestas/{propuesta_id}",
        tags=["propuestas"],
        response_model=PropuestaResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def obtener_propuesta(propuesta_id: str) -> PropuestaResponse:
        propuesta = app.state.servicio_propuestas.obtener(propuesta_id)
        if propuesta is None:
            raise HTTPException(status_code=404, detail="propuesta no encontrada")
        return _respuesta(propuesta)

    @app.get(
        "/propuestas/{propuesta_id}/documento-firma",
        tags=["firma"],
        response_model=DocumentoFirmaResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    def documento_firma(propuesta_id: str) -> DocumentoFirmaResponse:
        """CA-W03-02: lo que el cliente lee antes de firmar, armado desde la propuesta guardada."""
        try:
            documento = app.state.servicio_firma.documento(propuesta_id)
        except PropuestaNoEncontrada as error:
            raise HTTPException(status_code=404, detail="propuesta no encontrada") from error
        except FirmaNoPermitida as error:
            raise HTTPException(status_code=409, detail=error.motivo) from error
        return DocumentoFirmaResponse(**documento)

    @app.post(
        "/propuestas/{propuesta_id}/firma",
        tags=["firma"],
        response_model=FirmaResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse, "description": "no firmable o documento desactualizado"},
            422: {"model": ErrorResponse, "description": "COBRO_NO_AUTORIZADO"},
            503: {"model": ErrorResponse, "description": "el proveedor no responde; reintentar"},
        },
    )
    def firmar_propuesta(propuesta_id: str, body: FirmarRequest) -> FirmaResponse:
        """CA-W03-03: firma con el proveedor (vía el ACL) y guarda la evidencia de no repudio.
        Idempotente: firmar dos veces devuelve la misma evidencia."""
        try:
            evidencia, estado = app.state.servicio_firma.firmar(
                propuesta_id, body.autoriza_cobro, body.version_documento, body.firmante_id
            )
        except PropuestaNoEncontrada as error:
            raise HTTPException(status_code=404, detail="propuesta no encontrada") from error
        except FirmaNoPermitida as error:
            codigo = {
                COBRO_NO_AUTORIZADO: 422,
                SERVICIO_FIRMA_NO_DISPONIBLE: 503,
                PROPUESTA_NO_FIRMABLE: 409,
            }.get(error.motivo, 409)
            raise HTTPException(status_code=codigo, detail=error.motivo) from error
        return FirmaResponse(
            propuesta_id=propuesta_id,
            firmada_en=evidencia["firmada_en"],
            referencia=evidencia["referencia_proveedor"],
            estado=estado,
        )

    @app.post(
        "/propuestas/{propuesta_id}/_emitir",
        tags=["operacion"],
        response_model=EmisionResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def emitir_propuesta(propuesta_id: str) -> EmisionResponse:
        """La llama Cloud Tasks (`DISPARADOR=externo`) tras firmar, y de nuevo mientras el primer
        cobro siga pendiente. Es privada (IAM de Cloud Run) e idempotente. Responde 503 solo si
        UNDER o PAYMENTS no respondieron: así Cloud Tasks reintenta; nunca por un rechazo de
        negocio."""
        try:
            estado = app.state.servicio_emision.emitir(propuesta_id)
        except PropuestaNoEncontradaParaEmitir as error:
            raise HTTPException(status_code=404, detail="propuesta no encontrada") from error
        except EmisionPendiente as error:
            raise HTTPException(status_code=503, detail="EMISION_PENDIENTE") from error
        return EmisionResponse(estado=estado)

    @app.post(
        "/propuestas/{propuesta_id}/_reconciliar-cobro",
        tags=["operacion"],
        response_model=EmisionResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def reconciliar_cobro_propuesta(propuesta_id: str) -> EmisionResponse:
        """La llama el BFF cuando el back-office reintenta un cobro: si el pago de esa propuesta
        ya se cobró, emite la póliza (decisión W03-14; el cliente no puede dispararla). Privada
        (IAM de Cloud Run) e idempotente. 404 si el id no es de una propuesta (un cobro que no es
        de W03); 503 solo si PAYMENTS no respondió."""
        try:
            estado = app.state.servicio_emision.reconciliar_cobro(propuesta_id)
        except PropuestaNoEncontradaParaEmitir as error:
            raise HTTPException(status_code=404, detail="propuesta no encontrada") from error
        except EmisionPendiente as error:
            raise HTTPException(status_code=503, detail="EMISION_PENDIENTE") from error
        return EmisionResponse(estado=estado)

    @app.post(
        "/reconciliacion/cobros",
        tags=["operacion"],
        response_model=ResumenReconciliacionResponse,
    )
    def reconciliar_cobros() -> ResumenReconciliacionResponse:
        """Una pasada del proceso de reconciliación: revisa un lote de propuestas con el pago
        rechazado o pendiente y emite la póliza de las que ya se cobraron. La dispara Cloud
        Scheduler (privada, IAM de Cloud Run) o quien opere el servicio. Responde 200 aunque
        alguna propuesta falle: el resumen dice cuántas y la próxima pasada las retoma."""
        return ResumenReconciliacionResponse(**app.state.servicio_emision.reconciliar_pendientes())

    @app.get(
        "/polizas/{poliza_id}",
        tags=["polizas"],
        response_model=PolizaResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def obtener_poliza(poliza_id: str) -> PolizaResponse:
        poliza = app.state.servicio_polizas.obtener(poliza_id)
        if poliza is None:
            raise HTTPException(status_code=404, detail="póliza no encontrada")
        return PolizaResponse(**poliza)

    @app.post(
        "/polizas/{poliza_id}/documento",
        tags=["polizas"],
        response_class=Response,
        responses={200: {"content": {"application/pdf": {}}}, 404: {"model": ErrorResponse}},
    )
    def documento_poliza(poliza_id: str, body: DocumentoRequest) -> Response:
        """CA-W03-08: el PDF de la póliza, generado al pedirlo. Es un POST para que el nombre del
        asegurado viaje en el cuerpo y no en la URL. Quién puede pedirlo lo decide el BFF."""
        try:
            pdf = app.state.servicio_polizas.documento(poliza_id, body.asegurado)
        except DocumentoNoDisponible as error:
            raise HTTPException(status_code=503, detail="DOCUMENTO_NO_DISPONIBLE") from error
        if pdf is None:
            raise HTTPException(status_code=404, detail="póliza no encontrada")
        return Response(
            content=pdf, media_type="application/pdf", headers={"Cache-Control": "no-store"}
        )

    # ------------------------------------------------------------------ bloque 9 (W03-19 a W03-37)
    # Las rutas de la página del enlace reciben `X-Enlace-Firma` (el token) o `X-Titular-Id` (el
    # titular de la sesión del CLIENTE, que pone el BFF). Sin ninguno que coincida: 404.

    def _auth(enlace: str | None, titular: str | None) -> Autorizacion:
        return Autorizacion(enlace_token=enlace, titular_id=titular)

    def _flujo() -> ServicioFirmaYPago:
        return app.state.servicio_firma_y_pago

    @app.get("/propuestas", tags=["propuestas"], response_model=PaginaPropuestasResponse)
    def listar_propuestas(
        cliente_id: str | None = None,
        actor_usuario_id: str | None = None,
        estado_visible: str | None = None,
        pagina: int = 1,
        tamano: int = 10,
        actor_rol: str | None = None,
    ) -> PaginaPropuestasResponse:
        """W03-35. Quién ve qué lo decide el BFF por el rol del token: CLIENTE por `cliente_id` (su
        titular), ASESOR por `actor_usuario_id`, BACKOFFICE sin filtro. Un SOCIO (HU-W07), por
        `actor_rol=SOCIO` y su id en `actor_usuario_id`."""
        if estado_visible not in (None, "EMITIENDO", "EMITIDA", "NO_EMITIDA"):
            raise HTTPException(status_code=422, detail="estado_visible inválido")
        pagina, tamano = max(1, pagina), max(1, min(tamano, 50))
        propuestas, total = app.state.servicio_propuestas.listar(
            cliente_id, actor_usuario_id, estado_visible, pagina, tamano, actor_rol
        )
        return PaginaPropuestasResponse(
            propuestas=[
                PropuestaListadaResponse(
                    **{k: v for k, v in p.items() if k != "poliza"},
                    estado_visible=estado_visible_de(p["estado"]),
                    poliza=_poliza_resumen(p.get("poliza")),
                )
                for p in propuestas
            ],
            total=total,
            pagina=pagina,
            tamano=tamano,
        )

    estado_visible_de = estado_visible

    @app.get("/socios/{socio_id}/ventas", tags=["socios"], response_model=VentasSocioResponse)
    def ventas_socio(
        socio_id: str, desde: date | None = None, hasta: date | None = None
    ) -> VentasSocioResponse:
        """HU-W07 (`GET /v1/ventas`): propuestas, pólizas emitidas, primas y comisión por mes. Solo
        lo llama el BFF con el id del socio del token."""
        try:
            return VentasSocioResponse(
                **app.state.servicio_propuestas.ventas_socio(socio_id, desde, hasta)
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/propuestas/{propuesta_id}/codigo", tags=["firma"], response_model=CodigoResponse)
    def enviar_codigo(propuesta_id: str, body: CodigoRequest) -> CodigoResponse:
        """W03-20 (solo ASESOR, lo exige el BFF): código de 6 dígitos al correo del titular."""
        return CodigoResponse(**_flujo().enviar_codigo(propuesta_id, body.correo))

    @app.post(
        "/propuestas/{propuesta_id}/codigo/verificar",
        tags=["firma"],
        response_model=PropuestaResponse,
    )
    def verificar_codigo(propuesta_id: str, body: VerificarCodigoRequest) -> PropuestaResponse:
        return _respuesta(_flujo().verificar_codigo(propuesta_id, body.codigo))

    @app.post(
        "/propuestas/{propuesta_id}/emitir",
        tags=["firma"],
        response_model=EmisionPropuestaResponse,
    )
    def emitir(propuesta_id: str, body: EmitirRequest) -> EmisionPropuestaResponse:
        """«Emitir» del ASESOR o «Firmar» del CLIENTE titular: UNDER y KYC deciden (W03-21)."""
        resultado = _flujo().emitir(propuesta_id, body.rol, body.titular_id)
        enlace = resultado["enlace"]
        return EmisionPropuestaResponse(
            propuesta=_respuesta(resultado["propuesta"]),
            enlace=EnlaceResponse(**enlace) if enlace else None,
        )

    @app.post(
        "/propuestas/{propuesta_id}/enlace",
        tags=["firma"],
        response_model=EmisionPropuestaResponse,
    )
    def reenviar_enlace(propuesta_id: str) -> EmisionPropuestaResponse:
        resultado = _flujo().reenviar_enlace(propuesta_id)
        return EmisionPropuestaResponse(
            propuesta=_respuesta(resultado["propuesta"]),
            enlace=EnlaceResponse(**resultado["enlace"]),
        )

    @app.post("/enlaces/resolver", tags=["firma"], response_model=ResolverEnlaceResponse)
    def resolver_enlace(body: ResolverEnlaceRequest) -> ResolverEnlaceResponse:
        """El token va en el cuerpo, nunca en la URL (regla 6)."""
        resuelto = _flujo().resolver_enlace(body.token)
        if resuelto is None:
            raise HTTPException(status_code=404, detail="PROPUESTA_NO_ENCONTRADA")
        return ResolverEnlaceResponse(**resuelto)

    @app.get(
        "/propuestas/{propuesta_id}/enlace-estado",
        tags=["firma"],
        response_model=EstadoEnlaceResponse,
    )
    def estado_enlace(
        propuesta_id: str,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> EstadoEnlaceResponse:
        datos = _flujo().estado_para_enlace(propuesta_id, _auth(x_enlace_firma, x_titular_id))
        evidencia = datos["evidencia"]
        return EstadoEnlaceResponse(
            propuesta=_respuesta(datos["propuesta"]),
            vigente=datos["vigente"],
            vence_en=datos["vence_en"],
            firma=EvidenciaResponse(
                referencia=evidencia["referencia_proveedor"],
                firmada_en=evidencia["firmada_en"],
                modo_firma=evidencia.get("modo_firma"),
            )
            if evidencia
            else None,
        )

    @app.get(
        "/propuestas/{propuesta_id}/contrato",
        tags=["firma"],
        response_model=DocumentoFirmaResponse,
    )
    def contrato(
        propuesta_id: str,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> DocumentoFirmaResponse:
        documento = _flujo().documento(propuesta_id, _auth(x_enlace_firma, x_titular_id))
        return DocumentoFirmaResponse(**documento)

    @app.get(
        "/propuestas/{propuesta_id}/contrato.pdf",
        tags=["firma"],
        response_class=Response,
        responses={200: {"content": {"application/pdf": {}}}},
    )
    def contrato_pdf(
        propuesta_id: str,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> Response:
        """El contrato a firmar en PDF (W03-31). No lleva datos personales."""
        propuesta = _flujo().autorizar(propuesta_id, _auth(x_enlace_firma, x_titular_id))
        return Response(
            content=app.state.servicio_polizas.borrador(propuesta),
            media_type="application/pdf",
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/propuestas/{propuesta_id}/firma-imagen",
        tags=["firma"],
        response_model=SubidaFirmaResponse,
    )
    def subida_firma(
        propuesta_id: str,
        body: SubidaFirmaRequest,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> SubidaFirmaResponse:
        """URL firmada para subir la imagen de la firma directo al bucket (plan principal 1.5)."""
        subida = _flujo().solicitar_subida(
            propuesta_id,
            _auth(x_enlace_firma, x_titular_id),
            body.tipo_contenido,
            body.tamano_bytes,
        )
        return SubidaFirmaResponse(
            archivo=subida.archivo,
            url=subida.url,
            metodo=subida.metodo,
            encabezados=subida.encabezados,
            vence_en=subida.vence_en,
        )

    @app.post(
        "/propuestas/{propuesta_id}/firma-enlace", tags=["firma"], response_model=FirmaResponse
    )
    def firmar_por_enlace(
        propuesta_id: str,
        body: FirmarEnlaceRequest,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> FirmaResponse:
        evidencia, estado = _flujo().firmar(
            propuesta_id,
            _auth(x_enlace_firma, x_titular_id),
            body.version_documento,
            body.acepta_terminos,
            body.autoriza_cobro,
            body.modo,
            body.imagen_archivo_id,
            body.nombre_firma,
        )
        return FirmaResponse(
            propuesta_id=propuesta_id,
            firmada_en=evidencia["firmada_en"],
            referencia=evidencia["referencia_proveedor"],
            estado=estado,
        )

    @app.post("/propuestas/{propuesta_id}/pago", tags=["pago"], response_model=PagoResponse)
    def iniciar_pago(
        propuesta_id: str,
        body: PagoRequest,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> PagoResponse:
        """Abre (o reabre) la sesión de pago en la pasarela (W03-28). Nunca 5xx por la pasarela."""
        return _pago(
            _flujo().iniciar_pago(
                propuesta_id, _auth(x_enlace_firma, x_titular_id), body.url_retorno, body.idioma
            )
        )

    @app.post(
        "/propuestas/{propuesta_id}/pago/{sesion_id}/confirmar",
        tags=["pago"],
        response_model=PagoResponse,
    )
    def confirmar_pago(
        propuesta_id: str,
        sesion_id: str,
        x_enlace_firma: str | None = Header(default=None),
        x_titular_id: str | None = Header(default=None),
    ) -> PagoResponse:
        """Al regresar de la pasarela: confirma con ella y, si se cobró, emite la póliza en la misma
        respuesta (W03-29)."""
        return _pago(
            _flujo().confirmar_pago(propuesta_id, _auth(x_enlace_firma, x_titular_id), sesion_id)
        )

    @app.post("/propuestas/consentimiento-revocado", tags=["operacion"], status_code=204)
    def consentimiento_revocado(body: RevocacionRequest) -> Response:
        """W03-26: lo llama el BFF al revocar. Siempre 204 e idempotente."""
        app.state.servicio_purga.consentimiento_revocado(body.titular_id, body.consentimiento_id)
        return Response(status_code=204)

    @app.post("/_purgar-contactos", tags=["operacion"], response_model=ResumenPurgaResponse)
    def purgar_contactos() -> ResumenPurgaResponse:
        """W03-26: pasada diaria (Cloud Scheduler) por los consentimientos vencidos."""
        return ResumenPurgaResponse(**app.state.servicio_purga.purgar_vencidos())

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
