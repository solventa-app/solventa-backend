"""RISK: único escritor del perfil de riesgo."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query, Response
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from pymongo import MongoClient

from app.application.bus_eventos import construir_bus_eventos
from app.application.cache_score import CacheScore
from app.application.cifrado import CifradorAesGcm
from app.application.configuracion import Configuracion
from app.application.consultas import (
    TAMANO_MAXIMO,
    RepositorioConsultasMongo,
    ServicioConsultas,
)
from app.application.motor_decision import ResultadoDecision
from app.application.perfiles_vehiculo import (
    RepositorioPerfilesVehiculoMongo,
    ServicioPerfilesVehiculo,
)
from app.application.repositorio_perfiles import RepositorioPerfilesMongo
from app.application.servicio_perfiles import ServicioPerfiles
from app.observabilidad import configurar_trazas

try:
    import redis  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - entorno sin redis instalado
    redis = None  # type: ignore[assignment]

# Sin esto, los logs INFO de app.application.servicio_perfiles/bus_eventos se
# descartan en silencio (mismo motivo documentado en acl-worker/app/main.py).
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("risk", instrumentos=("httpx", "pymongo"))


class DatosFuenteEntrada(BaseModel):
    """Mismo shape que ya devuelve `acl-worker` en `POST /fuentes/consultar`
    (`DatosFuenteResponse`): no se inventa un contrato nuevo, se reusa."""

    fuente: str
    datos: dict
    capturado_en: str
    de_cache: bool = False
    degradado: bool = False


class CrearPerfilRequest(BaseModel):
    cliente_id: str
    fuentes: list[DatosFuenteEntrada]
    # W01-A3: opcionales para no romper a la revisión anterior del BFF durante el despliegue.
    consentimiento_id: str | None = None
    consentimiento_vence_en: datetime | None = None


class CrearPerfilVehiculoRequest(BaseModel):
    """KAN-290: las respuestas del ACL Worker (`POST /vehiculos/consultar`) para una placa.
    `vehiculo_id` es el HMAC de la placa (lo calcula el BFF); la placa nunca llega a RISK."""

    vehiculo_id: str
    titular_id: str
    consentimiento_id: str
    consentimiento_vence_en: datetime | None = None
    fuentes: list[DatosFuenteEntrada]


class CrearPerfilVehiculoResponse(BaseModel):
    vehiculo_id: str
    perfil_version: int
    operation_time: str
    completo: bool
    encontrado: bool


class FuenteConsultadaEntrada(BaseModel):
    """Una fila de `Oferta.fuentes` (contrato `FuenteConsultada`): su estado, nunca sus datos."""

    id: str
    nombre: str
    origen: str
    estado: str
    capturado_en: str | None = None


class RegistrarConsultaRequest(BaseModel):
    """QA 2026-11-10 (Q-08): lo que el BFF registra tras cotizar. `actor_id` es el asesor (sale de
    su token); el documento llega enmascarado y el titular como su HMAC."""

    actor_id: str
    actor_rol: str
    titular_id: str
    documento_enmascarado: str
    consentimiento_id: str
    tipo_oferta: str
    producto: str
    oferta_id: str | None = None
    fuentes: list[FuenteConsultadaEntrada]


class RegistrarConsultaResponse(BaseModel):
    id: str


class ConsultaResponse(BaseModel):
    id: str
    creada_en: str
    documento_enmascarado: str
    tipo_oferta: str
    producto: str
    oferta_id: str | None = None
    fuentes: list[FuenteConsultadaEntrada]


class PaginaConsultasResponse(BaseModel):
    consultas: list[ConsultaResponse]
    total: int
    pagina: int
    tamano: int


class ConsentimientoRevocadoRequest(BaseModel):
    """Mismo cuerpo que `POST /propuestas/consentimiento-revocado` de POLICY."""

    titular_id: str
    consentimiento_id: str


class PurgaResponse(BaseModel):
    perfiles_borrados: int


class CrearPerfilResponse(BaseModel):
    cliente_id: str
    perfil_version: int
    operation_time: str
    score: int = 50
    nivel_riesgo: str = "MEDIO"
    fecha_vigencia: str = ""
    clasificacion: str | None = None


class EvaluarPreaprobacionRequest(BaseModel):
    cliente_id: str
    monto: Decimal
    plazo_meses: int
    fuentes: list[DatosFuenteEntrada]


def create_app(
    config: Configuracion | None = None,
    servicio_perfiles: ServicioPerfiles | None = None,
    servicio_vehiculos: ServicioPerfilesVehiculo | None = None,
    servicio_consultas: ServicioConsultas | None = None,
) -> FastAPI:
    """`servicio_perfiles` permite inyectar un `ServicioPerfiles` ya
    construido (p. ej. con dobles de prueba) sin levantar MongoDB — así las
    pruebas unitarias no dependen de un replica set real (ver
    `tests/fakes.py`). En producción (o al verificar en vivo con
    `docker compose`) se omite y `create_app` construye todo desde
    `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_mongo: MongoClient | None = None
        cliente_redis = None
        app.state.servicio_vehiculos = servicio_vehiculos
        app.state.servicio_consultas = servicio_consultas
        if servicio_perfiles is not None:
            app.state.servicio_perfiles = servicio_perfiles
        else:
            cliente_mongo = MongoClient(config.mongo_uri)
            cifrador = CifradorAesGcm.desde_entorno(
                cifrado_clave=config.cifrado_clave,
                kms_key_name=config.kms_key_name,
                cifrado_clave_envuelta=config.cifrado_clave_envuelta,
            )
            if redis is not None and config.redis_url:
                try:
                    cliente_redis = redis.Redis.from_url(config.redis_url)
                except Exception:
                    cliente_redis = None
            cache_score = CacheScore(cliente_redis, config.score_cache_ttl_segundos)
            bus = construir_bus_eventos(config.pubsub_tema_perfil_actualizado)
            app.state.servicio_perfiles = ServicioPerfiles(
                RepositorioPerfilesMongo(cliente_mongo, cifrador=cifrador),
                bus,
                cache_score=cache_score,
            )
            if app.state.servicio_vehiculos is None:
                app.state.servicio_vehiculos = ServicioPerfilesVehiculo(
                    RepositorioPerfilesVehiculoMongo(cliente_mongo, cifrador)
                )
            if app.state.servicio_consultas is None:
                app.state.servicio_consultas = ServicioConsultas(
                    RepositorioConsultasMongo(cliente_mongo)
                )
        try:
            yield
        finally:
            if cliente_mongo is not None:
                cliente_mongo.close()
            if cliente_redis is not None:
                try:
                    cliente_redis.close()
                except Exception:
                    pass

    app = FastAPI(title="solventa-risk", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "risk", "estado": "ok"}

    @app.post("/perfiles", tags=["perfiles"], response_model=CrearPerfilResponse)
    def crear_perfil(body: CrearPerfilRequest) -> CrearPerfilResponse:
        resultado = app.state.servicio_perfiles.registrar(
            body.cliente_id,
            [fuente.model_dump() for fuente in body.fuentes],
            consentimiento_id=body.consentimiento_id,
            consentimiento_vence_en=body.consentimiento_vence_en,
        )
        return CrearPerfilResponse(**resultado)

    @app.post("/perfiles/consentimiento-revocado", tags=["perfiles"], status_code=204)
    def consentimiento_revocado(body: ConsentimientoRevocadoRequest) -> Response:
        app.state.servicio_perfiles.consentimiento_revocado(body.titular_id, body.consentimiento_id)
        # KAN-290: también el perfil del vehículo que se armó con ese consentimiento.
        if app.state.servicio_vehiculos is not None:
            app.state.servicio_vehiculos.consentimiento_revocado(
                body.titular_id, body.consentimiento_id
            )
        return Response(status_code=204)

    @app.post("/perfiles/_purgar", tags=["operacion"], response_model=PurgaResponse)
    def purgar_perfiles() -> PurgaResponse:
        """Lo llama Cloud Scheduler una vez al día."""
        borrados = app.state.servicio_perfiles.purgar_vencidos()
        if app.state.servicio_vehiculos is not None:
            borrados += app.state.servicio_vehiculos.purgar_vencidos()
        return PurgaResponse(perfiles_borrados=borrados)

    @app.post("/vehiculos/perfiles", tags=["perfiles"], response_model=CrearPerfilVehiculoResponse)
    def crear_perfil_vehiculo(body: CrearPerfilVehiculoRequest) -> CrearPerfilVehiculoResponse:
        """KAN-290: guarda el perfil de riesgo del vehículo (cifrado) y devuelve su versión y el
        `operation_time` para que RATING lo lea de la secundaria con sesión causal."""
        resultado = app.state.servicio_vehiculos.registrar(
            body.vehiculo_id,
            body.titular_id,
            body.consentimiento_id,
            [fuente.model_dump() for fuente in body.fuentes],
            consentimiento_vence_en=body.consentimiento_vence_en,
        )
        return CrearPerfilVehiculoResponse(**resultado)

    def _consultas() -> ServicioConsultas:
        if app.state.servicio_consultas is None:
            raise HTTPException(status_code=503, detail="consultas_no_disponibles")
        return app.state.servicio_consultas

    @app.post("/consultas", tags=["consultas"], response_model=RegistrarConsultaResponse)
    def registrar_consulta(body: RegistrarConsultaRequest) -> RegistrarConsultaResponse:
        """QA 2026-11-10 (Q-08): deja el renglón del historial de fuentes de un asesor."""
        consulta_id = _consultas().registrar(
            body.actor_id,
            body.actor_rol,
            body.titular_id,
            body.documento_enmascarado,
            body.consentimiento_id,
            body.tipo_oferta,
            body.producto,
            [f.model_dump() for f in body.fuentes],
            oferta_id=body.oferta_id,
        )
        return RegistrarConsultaResponse(id=consulta_id)

    @app.get("/consultas", tags=["consultas"], response_model=PaginaConsultasResponse)
    def listar_consultas(
        actor_id: str,
        pagina: int = Query(1, ge=1),
        tamano: int = Query(10, ge=1, le=TAMANO_MAXIMO),
    ) -> dict:
        """Las consultas del actor, de la más reciente a la más antigua."""
        return _consultas().listar(actor_id, pagina, tamano)

    @app.get("/consultas/{consulta_id}", tags=["consultas"], response_model=ConsultaResponse)
    def obtener_consulta(consulta_id: str, actor_id: str) -> dict:
        """Una consulta del actor; una ajena responde igual que una inexistente."""
        consulta = _consultas().obtener(consulta_id, actor_id)
        if consulta is None:
            raise HTTPException(status_code=404, detail="consulta_no_encontrada")
        return consulta

    @app.post(
        "/preaprobaciones/evaluar", tags=["preaprobaciones"], response_model=ResultadoDecision
    )
    def evaluar_preaprobacion(body: EvaluarPreaprobacionRequest) -> ResultadoDecision:
        fuentes = [fuente.model_dump() for fuente in body.fuentes]
        return app.state.servicio_perfiles.evaluar_preaprobacion(
            cliente_id=body.cliente_id,
            monto=body.monto,
            plazo_meses=body.plazo_meses,
            fuentes=fuentes,
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
