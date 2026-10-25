"""POLICY: propuestas y pólizas (HU-W03).

Único escritor de las tablas de suscripción (regla de arquitectura #1). Lee la oferta de RATING y
nunca llama a un proveedor externo directamente (regla #2): la firma, el KYC y las notificaciones
pasan por el ACL Worker en los bloques siguientes.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from app.application.autenticacion_servicios import autenticacion_para
from app.application.bus_eventos import BusEventosLog
from app.application.cifrado import cifrador_desde_entorno
from app.application.cliente_acl import ClienteAcl
from app.application.cliente_auth import ClienteAuth
from app.application.cliente_payments import ClientePayments
from app.application.cliente_rating import ClienteRating, RatingNoDisponible
from app.application.cliente_under import ClienteUnder
from app.application.configuracion import Configuracion
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
from app.application.servicio_polizas import ServicioPolizas
from app.application.servicio_propuestas import (
    OFERTA_NO_ENCONTRADA,
    OfertaNoAceptable,
    ServicioPropuestas,
)
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("policy", instrumentos=("httpx", "psycopg"))


class AceptarOfertaRequest(BaseModel):
    oferta_id: str = Field(min_length=1)
    # El usuario (CLIENTE o ASESOR) que aceptó; solo su id opaco, para el rastro de quién hizo qué.
    actor_usuario_id: str | None = Field(default=None, min_length=1)


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
    return PropuestaResponse(**propuesta)


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
        if servicio_propuestas is not None:
            app.state.servicio_propuestas = servicio_propuestas
            app.state.servicio_firma = servicio_firma
            app.state.servicio_emision = servicio_emision
            app.state.servicio_polizas = servicio_polizas
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
            planificador = _planificador(config)
            servicio_de_polizas = ServicioPolizas(repositorio, config.documento_ambiente_pruebas)
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
            )
            if isinstance(planificador, PlanificadorLocal):
                planificador.conectar(emision.emitir)
            app.state.servicio_propuestas = ServicioPropuestas(repositorio, cliente_rating)
            app.state.servicio_firma = ServicioFirma(repositorio, cliente_acl, planificador)
            app.state.servicio_emision = emision
            app.state.servicio_polizas = servicio_de_polizas
        try:
            yield
        finally:
            for cliente in (cliente_under, cliente_payments, cliente_auth):
                if cliente is not None:
                    cliente.cerrar()
            if cliente_acl is not None:
                cliente_acl.cerrar()
            if cliente_rating is not None:
                cliente_rating.cerrar()
            if pool is not None:
                pool.close()

    app = FastAPI(title="solventa-policy", version="0.1.0", lifespan=lifespan)

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
            propuesta = app.state.servicio_propuestas.aceptar(body.oferta_id, body.actor_usuario_id)
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
        pdf = app.state.servicio_polizas.documento(poliza_id, body.asegurado)
        if pdf is None:
            raise HTTPException(status_code=404, detail="póliza no encontrada")
        return Response(
            content=pdf, media_type="application/pdf", headers={"Cache-Control": "no-store"}
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
