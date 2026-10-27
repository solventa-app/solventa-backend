"""ALMACENAMIENTO: metadatos, permisos y URLs firmadas de los archivos (habilitadora KAN-250).

Único escritor de `archivos` (regla 1). Los bytes van directo entre el cliente y Cloud Storage con
URLs firmadas: nunca pasan por aquí ni por el gateway. Servicio privado: lo llaman el BFF y los
servicios que declaren `"llama": ["almacenamiento"]` en `.github/servicios.json`, y Pub/Sub con las
notificaciones del bucket. Cómo conectar una HU: `docs/almacenamiento.md`.

Cada operación de un usuario o servicio declara a nombre de quién actúa con los encabezados
`X-Actor-Rol` (`CLIENTE`, `ASESOR`, `BACKOFFICE` o `SERVICIO`) y `X-Actor-Id` (el id opaco del
usuario, o el nombre del servicio)."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from app.application.almacen_objetos import AlmacenGcs
from app.application.bus_eventos import construir_bus
from app.application.catalogo import Catalogo
from app.application.configuracion import Configuracion
from app.application.identificadores import id_opaco
from app.application.repositorio_archivos import RepositorioArchivosPostgres, construir_pool
from app.application.servicio_archivos import Actor, ErrorArchivo, ServicioArchivos, Vigencias
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("almacenamiento", instrumentos=("psycopg",))

logger = logging.getLogger("almacenamiento")


class SolicitudSubidaRequest(BaseModel):
    tipo: str = Field(min_length=1, max_length=64)
    tipo_contenido: str = Field(min_length=1, max_length=100)
    tamano_bytes: int = Field(gt=0)
    propietario_id: str = Field(min_length=1, max_length=100)
    consentimiento_id: str | None = Field(default=None, min_length=1, max_length=100)
    derivado_de: str | None = Field(default=None, min_length=1, max_length=100)


class ArchivoResponse(BaseModel):
    id: str
    tipo: str
    estado: str
    propietario_tipo: str
    propietario_id: str
    tipo_contenido: str
    tamano_bytes: int | None = None
    crc32c: str | None = None
    derivado_de: str | None = None
    # Por qué se rechazó (RECHAZADO): un código, nunca el contenido.
    motivo: str | None = None
    retener_desde: datetime | None = None
    creado_en: datetime
    disponible_en: datetime | None = None
    eliminado_en: datetime | None = None


class SubidaResponse(BaseModel):
    archivo: ArchivoResponse
    url: str
    metodo: str
    # Encabezados que el cliente DEBE mandar tal cual en el PUT (van firmados).
    encabezados: dict[str, str]
    vence_en: datetime


class DescargaResponse(BaseModel):
    url: str
    vence_en: datetime


class PurgaResponse(BaseModel):
    eliminados: int


class ErrorResponse(BaseModel):
    detail: str


def _archivo(fila: dict[str, Any]) -> ArchivoResponse:
    return ArchivoResponse(**{k: v for k, v in fila.items() if k in ArchivoResponse.model_fields})


def _actor(rol: str | None, actor_id: str | None) -> Actor:
    try:
        return Actor((rol or "").upper(), actor_id or None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="ACTOR_INVALIDO") from error


RolActor = Annotated[str | None, Header(alias="X-Actor-Rol")]
IdActor = Annotated[str | None, Header(alias="X-Actor-Id")]


def create_app(
    config: Configuracion | None = None,
    servicio: ServicioArchivos | None = None,
    bucket: str | None = None,
) -> FastAPI:
    """`servicio` permite inyectar dobles de prueba sin PostgreSQL ni Cloud Storage (ver
    `tests/fakes.py`). En producción se omite y se construye desde `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: ConnectionPool | None = None
        app.state.bucket = bucket if bucket is not None else config.bucket
        if servicio is not None:
            app.state.servicio = servicio
        elif not config.bucket:
            logger.warning("ALMACEN_BUCKET vacío: el servicio responde 503 hasta configurarlo")
            app.state.servicio = None
        else:
            pool = construir_pool(config.database_url)
            almacen = AlmacenGcs(config.bucket, config.firmante, config.url_publica or None)
            if config.firmante == "local":
                almacen.asegurar_bucket()
            app.state.servicio = ServicioArchivos(
                RepositorioArchivosPostgres(pool),
                almacen,
                Catalogo.desde_archivo(),
                construir_bus(config.tema_disponible, config.tema_rechazado),
                Vigencias(config.vigencia_subida_s, config.vigencia_descarga_s),
            )
        try:
            yield
        finally:
            if pool is not None:
                pool.close()

    app = FastAPI(title="solventa-almacenamiento", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ErrorArchivo)
    async def _error_archivo(_: Request, error: ErrorArchivo) -> JSONResponse:
        return JSONResponse(status_code=error.estado_http, content={"detail": error.codigo})

    def _servicio() -> ServicioArchivos:
        if app.state.servicio is None:
            raise HTTPException(status_code=503, detail="ALMACEN_NO_CONFIGURADO")
        return app.state.servicio

    errores = {
        400: {"model": ErrorResponse, "description": "ACTOR_INVALIDO"},
        403: {"model": ErrorResponse, "description": "NO_AUTORIZADO"},
        404: {"model": ErrorResponse, "description": "ARCHIVO_NO_ENCONTRADO"},
        409: {"model": ErrorResponse, "description": "ARCHIVO_NO_DISPONIBLE u OBJETO_NO_SUBIDO"},
        422: {"model": ErrorResponse, "description": "la solicitud no cumple el catálogo"},
        503: {"model": ErrorResponse, "description": "ALMACEN_NO_CONFIGURADO"},
    }

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "almacenamiento", "estado": "ok"}

    @app.post(
        "/archivos/subidas", tags=["archivos"], response_model=SubidaResponse, responses=errores
    )
    def solicitar_subida(
        body: SolicitudSubidaRequest, rol: RolActor = None, actor_id: IdActor = None
    ) -> SubidaResponse:
        """Registra el archivo PENDIENTE y devuelve la URL firmada para subirlo directo al bucket.
        El cliente hace `PUT url` con `encabezados` exactamente como vienen."""
        resultado = _servicio().solicitar_subida(
            _actor(rol, actor_id),
            body.tipo,
            body.tipo_contenido,
            body.tamano_bytes,
            body.propietario_id,
            body.consentimiento_id,
            body.derivado_de,
        )
        return SubidaResponse(**{**resultado, "archivo": _archivo(resultado["archivo"])})

    @app.post(
        "/archivos/{archivo_id}/confirmar",
        tags=["archivos"],
        response_model=ArchivoResponse,
        responses=errores,
    )
    def confirmar(
        archivo_id: str, rol: RolActor = None, actor_id: IdActor = None
    ) -> ArchivoResponse:
        """Respaldo de la notificación del bucket (y el único camino en local): valida el objeto y
        lo deja DISPONIBLE o RECHAZADO. Idempotente. 409 OBJETO_NO_SUBIDO si aún no está."""
        return _archivo(_servicio().confirmar(archivo_id, _actor(rol, actor_id)))

    @app.get(
        "/archivos/{archivo_id}",
        tags=["archivos"],
        response_model=ArchivoResponse,
        responses=errores,
    )
    def obtener(archivo_id: str, rol: RolActor = None, actor_id: IdActor = None) -> ArchivoResponse:
        return _archivo(_servicio().obtener(_actor(rol, actor_id), archivo_id))

    @app.get(
        "/archivos/{archivo_id}/descarga",
        tags=["archivos"],
        response_model=DescargaResponse,
        responses=errores,
    )
    def descarga(
        archivo_id: str, rol: RolActor = None, actor_id: IdActor = None
    ) -> DescargaResponse:
        """URL firmada de lectura de unos minutos. Solo para archivos DISPONIBLE."""
        return DescargaResponse(**_servicio().descarga(_actor(rol, actor_id), archivo_id))

    @app.get(
        "/archivos", tags=["archivos"], response_model=list[ArchivoResponse], responses=errores
    )
    def listar(
        propietario_tipo: str,
        propietario_id: str,
        rol: RolActor = None,
        actor_id: IdActor = None,
    ) -> list[ArchivoResponse]:
        """Los archivos de un propietario que el actor puede leer (ids opacos en la URL)."""
        filas = _servicio().listar(_actor(rol, actor_id), propietario_tipo, propietario_id)
        return [_archivo(fila) for fila in filas]

    @app.post(
        "/archivos/{archivo_id}/retencion",
        tags=["archivos"],
        response_model=ArchivoResponse,
        responses=errores,
    )
    def retencion(
        archivo_id: str, rol: RolActor = None, actor_id: IdActor = None
    ) -> ArchivoResponse:
        """Desde ahora corre la retención del tipo: el objeto se borra `retencion_dias` después.
        Idempotente (la primera fecha gana)."""
        return _archivo(_servicio().fijar_retencion(_actor(rol, actor_id), archivo_id))

    @app.post("/eventos/gcs", tags=["eventos"], status_code=204)
    async def notificacion_gcs(request: Request) -> Response:
        """Push de Pub/Sub con una notificación del bucket (`OBJECT_FINALIZE` / `OBJECT_DELETE`).
        Privado (IAM de Cloud Run, token OIDC de la suscripción). Siempre 204 salvo que el servicio
        no esté configurado: un mensaje que no se entiende no se reintenta."""
        servicio_actual = _servicio()
        try:
            mensaje = (await request.json())["message"]
            atributos = mensaje.get("attributes", {})
            tipo_evento = atributos["eventType"]
            objeto = atributos["objectId"]
            bucket_evento = atributos.get("bucketId")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("notificación del bucket ilegible: se descarta")
            return Response(status_code=204)
        if bucket_evento and bucket_evento != app.state.bucket:
            return Response(status_code=204)
        resultado = servicio_actual.notificacion_objeto(tipo_evento, objeto)
        logger.info("notificación %s objeto=%s -> %s", tipo_evento, id_opaco(objeto), resultado)
        return Response(status_code=204)

    @app.post("/archivos/_purgar-vencidos", tags=["operacion"], response_model=PurgaResponse)
    def purgar_vencidos(limite: int = 100) -> PurgaResponse:
        """Borra los archivos cuya retención venció. En la nube lo hace el ciclo de vida del bucket;
        esto es el respaldo y lo que se usa en local (el emulador no tiene ciclo de vida)."""
        return PurgaResponse(eliminados=_servicio().purgar_vencidos(max(1, min(limite, 1000))))

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
