"""CLAIMS: escritor único de la mesa de siniestros (HU-W04).

Servicio privado: solo lo llama el BFF (IAM de Cloud Run). La autorización por rol (solo BACKOFFICE)
la hace el BFF antes de llamar (W04-11).
"""

import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.application.asistencias import (
    ErrorAsistencia,
    RepositorioAsistenciasMongo,
    ServicioAsistencias,
)
from app.application.autenticacion_servicios import autenticacion_para
from app.application.bus_eventos import BusEventosLog, validar_entrante
from app.application.catalogo_prestadores import (
    CatalogoNoDisponible,
    LectorCatalogoPrestadores,
    consultar,
)
from app.application.cifrado import CifradorAesGcm
from app.application.clientes import (
    ClienteAlmacenamiento,
    ClientePayments,
    ClientePolicy,
    reglas_de_reescritura,
)
from app.application.configuracion import Configuracion
from app.application.repositorio import RepositorioSiniestrosMongo
from app.application.servicio_parametricos import ServicioParametricos
from app.application.servicio_siniestros import (
    DependenciaNoDisponible,
    ErrorNegocio,
    ServicioSiniestros,
)
from app.observabilidad import configurar_trazas
from app.sembrar import reiniciar as reiniciar_semilla

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx registra cada URL pedida en INFO, y las URLs firmadas del almacenamiento no deben quedar en
# los logs (como en POLICY).
logging.getLogger("httpx").setLevel(logging.WARNING)

configurar_trazas("claims", instrumentos=("httpx", "pymongo"))


class EstadoSiniestro(StrEnum):
    PENDIENTE = "PENDIENTE"
    EN_REVISION = "EN_REVISION"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


class AprobarRequest(BaseModel):
    actor_usuario_id: str | None = None  # el BACKOFFICE que resuelve (id opaco de AUTH)
    # KAN-290: el valor del daño en un siniestro de auto parcial o de RC (decimal como texto).
    valor_dano: str | None = Field(default=None, pattern=r"^\d{1,13}(\.\d{1,2})?$")


class RechazarRequest(BaseModel):
    motivo: str
    comentario: str | None = Field(default=None, max_length=2000)
    actor_usuario_id: str | None = None


class UbicacionReporte(BaseModel):
    descripcion: str = Field(min_length=1, max_length=300)
    ciudad: str = Field(min_length=1, max_length=100)
    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)


class AseguradoReporte(BaseModel):
    nombre: str = Field(min_length=1)
    documento: str = Field(min_length=1)


class FotoReporte(BaseModel):
    archivo_id: str = Field(min_length=1)
    tomada_en: datetime
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)


class ReporteRequest(BaseModel):
    """HU-M05: lo arma el BFF con el CLIENTE ya autenticado (su id, su nombre y documento de AUTH)
    y lo que capturó la app. `id_reporte` lo genera la app y es el id del siniestro."""

    id_reporte: str = Field(min_length=8, max_length=64)
    cliente_id: str = Field(min_length=1)
    # El titular del cliente (HMAC de su documento, de AUTH): con él se reconocen sus pólizas.
    titular_id: str | None = None
    asegurado: AseguradoReporte
    poliza_id: str = Field(min_length=1)
    cobertura_aplicada: str = Field(min_length=1)
    # KAN-290: obligatorio si la póliza es de AUTO.
    tipo_siniestro_auto: (
        Literal[
            "CHOQUE_PARCIAL",
            "PERDIDA_TOTAL_DANOS",
            "HURTO_TOTAL",
            "HURTO_PARCIAL",
            "RESPONSABILIDAD_CIVIL",
        ]
        | None
    ) = None
    ocurrido_en: datetime
    descripcion: str
    ubicacion: UbicacionReporte
    evidencias: list[FotoReporte]


class TipoAsistencia(StrEnum):
    MEDICO = "MEDICO"
    MECANICO = "MECANICO"


class SolicitudAsistenciaRequest(BaseModel):
    """HU-M06: lo arma el BFF con el CLIENTE del token y lo que mandó la app. `id_solicitud` lo
    genera la app (UUID) y es el id de la asistencia: reenviar con el mismo no crea otra."""

    id_solicitud: str = Field(min_length=8, max_length=64)
    cliente_id: str = Field(min_length=1)
    prestador_id: str = Field(min_length=1, max_length=64)
    tipo_asistencia: TipoAsistencia
    descripcion: str | None = Field(default=None, max_length=2000)
    direccion: str | None = Field(default=None, max_length=1000)
    # Sin ubicación (CA-M06-02) la dirección es obligatoria: lo valida ServicioAsistencias.
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)


class CancelarAsistenciaRequest(BaseModel):
    cliente_id: str = Field(min_length=1)


class DerivarRequest(BaseModel):
    perito_id: str = Field(min_length=1)
    instrucciones: str | None = Field(default=None, max_length=2000)
    actor_usuario_id: str | None = None


def _decodificar_sobre_pubsub(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """KAN-259 (HU-M04, Fase 4): acepta el evento directo o el sobre push de Pub/Sub
    (`{"message": {"data": "<base64>"}}`), mismo patrón que `enviar-push/app/main.py`."""
    if not isinstance(cuerpo.get("message"), dict):
        return cuerpo
    data_b64 = cuerpo["message"].get("data")
    if not isinstance(data_b64, str) or not data_b64.strip():
        raise ValueError("mensaje Pub/Sub sin data en base64")
    try:
        return json.loads(base64.b64decode(data_b64).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"data de Pub/Sub invalida: {error}") from error


def create_app(
    config: Configuracion | None = None,
    servicio: ServicioSiniestros | None = None,
    reiniciar: Callable[[], int] | None = None,
    catalogo=None,
    asistencias: ServicioAsistencias | None = None,
    parametricos: ServicioParametricos | None = None,
) -> FastAPI:
    """`servicio`, `reiniciar`, `catalogo`, `asistencias` y `parametricos` permiten inyectar dobles
    de prueba sin levantar MongoDB (ver `tests/fakes.py`)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_mongo: MongoClient | None = None
        policy: ClientePolicy | None = None
        payments: ClientePayments | None = None
        almacenamiento: ClienteAlmacenamiento | None = None
        app.state.reiniciar = reiniciar
        app.state.catalogo = catalogo
        app.state.asistencias = asistencias
        app.state.parametricos = parametricos
        if servicio is not None:
            app.state.servicio = servicio
        else:
            cliente_mongo = MongoClient(config.mongo_uri, tz_aware=True)
            cifrador = CifradorAesGcm.desde_entorno(
                cifrado_clave=config.cifrado_clave,
                kms_key_name=config.kms_key_name,
                cifrado_clave_envuelta=config.cifrado_clave_envuelta,
            )
            repositorio = RepositorioSiniestrosMongo(
                cliente_mongo, cifrador, config.mongo_db, config.lectura_max_ms
            )
            if app.state.catalogo is None:
                app.state.catalogo = LectorCatalogoPrestadores(
                    cliente_mongo, config.mongo_db, config.lectura_max_ms
                )
            if app.state.asistencias is None:
                app.state.asistencias = ServicioAsistencias(
                    RepositorioAsistenciasMongo(cliente_mongo, cifrador, config.mongo_db),
                    simulacion=config.asistencia_simulacion,
                )
            modo = config.autenticacion_servicios
            policy = ClientePolicy(
                config.policy_url,
                config.servicios_timeout_segundos,
                autenticacion_para(modo, "policy"),
            )
            payments = ClientePayments(
                config.payments_url,
                config.servicios_timeout_segundos,
                autenticacion_para(modo, "payments"),
            )
            almacenamiento = ClienteAlmacenamiento(
                config.almacenamiento_url,
                config.servicios_timeout_segundos,
                autenticacion_para(modo, "almacenamiento"),
                reglas_de_reescritura(config.almacen_reescritura_origenes),
            )
            bus = BusEventosLog()
            app.state.servicio = ServicioSiniestros(
                repositorio,
                policy=policy,
                payments=payments,
                bus=bus,
                almacenamiento=almacenamiento,
            )
            if app.state.parametricos is None:
                # KAN-259 (HU-M04, Fase 4): mismo POLICY y PAYMENTS que la mesa de siniestros.
                app.state.parametricos = ServicioParametricos(repositorio, policy, payments, bus)
            if app.state.reiniciar is None:
                datos = Path(config.semilla_dir)
                app.state.reiniciar = lambda: reiniciar_semilla(repositorio, datos)
        try:
            yield
        finally:
            for cliente in (policy, payments, almacenamiento):
                if cliente is not None:
                    cliente.cerrar()
            if cliente_mongo is not None:
                cliente_mongo.close()

    app = FastAPI(title="solventa-claims", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "claims", "estado": "ok"}

    @app.get("/siniestros", tags=["siniestros"])
    def listar(estado: EstadoSiniestro | None = None) -> list[dict]:
        """Bandeja: del más reciente al más antiguo. Se lee de la réplica (HA-LAT-003)."""
        return app.state.servicio.listar(estado.value if estado else None)

    @app.get("/siniestros/{siniestro_id}", tags=["siniestros"])
    def obtener(siniestro_id: str) -> dict:
        expediente = app.state.servicio.obtener(siniestro_id)
        if expediente is None:
            raise HTTPException(status_code=404, detail="siniestro no encontrado")
        return expediente

    @app.get("/siniestros/{siniestro_id}/peritos", tags=["siniestros"])
    def peritos(siniestro_id: str) -> list[dict]:
        return app.state.servicio.peritos_para(siniestro_id)

    @app.get("/prestadores", tags=["prestadores"])
    def prestadores(version_local: str | None = None) -> dict:
        """HU-M06: catálogo maestro de prestadores, versionado. Se lee de la réplica."""
        try:
            return consultar(app.state.catalogo, version_local)
        except (CatalogoNoDisponible, PyMongoError):
            # Sin catálogo cargado o con la réplica lenta: 503 y la app sigue con su copia.
            detalle = "El catálogo de prestadores no está disponible."
            return JSONResponse(status_code=503, content={"detail": detalle})

    def _accion(accion) -> dict | JSONResponse:
        """Un error de negocio es 409 y una dependencia caída 503, siempre con un `detail` en
        lenguaje del operador que el BFF muestra tal cual (nunca un 5xx crudo al cliente)."""
        try:
            return accion()
        except ErrorNegocio as error:
            return JSONResponse(status_code=409, content={"detail": str(error)})
        except DependenciaNoDisponible as error:
            return JSONResponse(status_code=503, content={"detail": str(error)})

    def _asistencia(accion) -> dict | JSONResponse:
        """409 con `codigo` estable (la app elige el texto) o 404 si no es del cliente."""
        try:
            return accion()
        except ErrorAsistencia as error:
            estado = 404 if error.codigo == "NO_ENCONTRADA" else 409
            contenido = {"detail": str(error), "codigo": error.codigo}
            return JSONResponse(status_code=estado, content=contenido)
        except PyMongoError:
            detalle = "No pudimos registrar la asistencia. Intenta de nuevo."
            return JSONResponse(status_code=503, content={"detail": detalle})

    @app.post("/asistencias", tags=["asistencia"])
    def solicitar_asistencia(body: SolicitudAsistenciaRequest) -> dict:
        """HU-M06 (KAN-265): idempotente por `id_solicitud`; reenviar una NO_CONFIRMADA
        reintenta."""
        return _asistencia(lambda: app.state.asistencias.solicitar(body.model_dump(mode="json")))

    @app.post("/asistencias/{asistencia_id}/cancelar", tags=["asistencia"])
    def cancelar_asistencia(asistencia_id: str, body: CancelarAsistenciaRequest) -> dict:
        """HU-M06 (KAN-266): idempotente; solo el cliente que la pidió."""
        return _asistencia(lambda: app.state.asistencias.cancelar(asistencia_id, body.cliente_id))

    @app.post("/siniestros", tags=["reporte"])
    def reportar(body: ReporteRequest) -> dict:
        """HU-M05: el cliente reporta desde la app y el caso llega PENDIENTE a la bandeja (W04-01).
        Idempotente por `id_reporte`."""
        return _accion(lambda: app.state.servicio.reportar(body.model_dump()))

    @app.get("/clientes/{cliente_id}/siniestros", tags=["reporte"])
    def de_cliente(cliente_id: str) -> list[dict]:
        """Los casos que reportó un cliente (el BFF pasa el id del token, nunca uno ajeno)."""
        return app.state.servicio.de_cliente(cliente_id)

    @app.post("/siniestros/_aprobar-vencidos", tags=["operacion"])
    def aprobar_vencidos() -> dict[str, int]:
        """Una pasada del plazo del dictamen. La dispara Cloud Scheduler; es idempotente."""
        return app.state.servicio.aprobar_vencidos()

    @app.post("/eventos/parametricos", tags=["operacion"])
    def eventos_parametricos(body: dict[str, Any]) -> dict[str, int]:
        """KAN-259 (HU-M04, Fase 4): consume `evento.parametrico.recibido` (lo publica el ACL
        Worker) y, si el umbral se cumple, liquida la indemnización de una vez (idempotente por
        `eventoId` + `polizaId`, W04-9). Privado (IAM de Cloud Run / suscripción push de Pub/Sub).

        400 = el evento no decodifica o no cumple su esquema: Pub/Sub no debe reintentarlo.
        200 = se procesó, sea que creó un siniestro o que no había nada que hacer (regla #4: un
        evento sin pólizas que califiquen, o bajo el umbral, no es un error).
        500 = un fallo interno inesperado (p. ej. POLIZA/PAYMENTS no responden): Pub/Sub reintenta
        con su backoff, correcto aquí porque este es un consumidor asíncrono, no el camino
        síncrono de un usuario."""
        try:
            evento = _decodificar_sobre_pubsub(body)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        errores = validar_entrante(evento)
        if errores:
            raise HTTPException(status_code=400, detail="; ".join(errores))
        return app.state.parametricos.procesar(evento)

    @app.post("/siniestros/{siniestro_id}/aprobar", tags=["resolucion"])
    def aprobar(siniestro_id: str, body: AprobarRequest) -> dict:
        """Registra la orden de pago en PAYMENTS y aprueba (W04-6)."""
        return _accion(
            lambda: app.state.servicio.aprobar(
                siniestro_id, body.actor_usuario_id, valor_dano=body.valor_dano
            )
        )

    @app.post("/siniestros/{siniestro_id}/rechazar", tags=["resolucion"])
    def rechazar(siniestro_id: str, body: RechazarRequest) -> dict:
        return _accion(
            lambda: app.state.servicio.rechazar(
                siniestro_id, body.motivo, body.comentario, body.actor_usuario_id
            )
        )

    @app.post("/siniestros/{siniestro_id}/derivar", tags=["resolucion"])
    def derivar(siniestro_id: str, body: DerivarRequest) -> dict:
        return _accion(
            lambda: app.state.servicio.derivar(
                siniestro_id, body.perito_id, body.instrucciones, body.actor_usuario_id
            )
        )

    if config.demo_reiniciar:

        @app.post("/_demo/reiniciar", tags=["operacion"])
        def reiniciar_demo() -> dict[str, int]:
            """Solo local (W04-15, `DEMO_REINICIAR=true`): deja los casos como en la semilla para
            las pruebas E2E de la web. Nunca existe en staging."""
            return {"siniestros": app.state.reiniciar()}

        @app.post("/_demo/siniestros/{siniestro_id}/vencer", tags=["operacion"])
        def vencer_demo(siniestro_id: str, minutos: int = Query(2, ge=0, le=1440)) -> dict:
            """Solo local (Q-03): el plazo del dictamen de un caso PENDIENTE vence en `minutos`.
            El disparador local (`aprobar-vencidos-local`) lo aprueba en el minuto siguiente."""
            return _accion(lambda: app.state.servicio.vencer_en(siniestro_id, minutos))

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
