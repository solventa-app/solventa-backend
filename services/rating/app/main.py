"""RATING: cálculo de oferta sobre réplica de lectura."""

import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import InvalidOperation
from typing import Any

import redis
from fastapi import FastAPI, HTTPException, Query
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from pymongo import MongoClient

from app.application.cache_score import CacheScore
from app.application.cifrado import CifradorAesGcm
from app.application.configuracion import Configuracion
from app.application.identificadores import id_opaco
from app.application.lector_perfiles import LectorPerfiles, LectorPerfilesMongo
from app.application.servicio_oferta import DatosInsuficientes, ServicioOferta
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("rating", instrumentos=("httpx", "pymongo", "redis"))

logger_eventos = logging.getLogger("rating.eventos")

# Contrato con el BFF: 422 con este `detail` = falta una fuente obligatoria (CA-W01-05).
DETALLE_DATOS_INSUFICIENTES = "datos_insuficientes"


class SolicitudOferta(BaseModel):
    cliente_id: str
    operation_time: str
    cobertura: str
    plazo_meses: int | None = None
    direccion_inmueble: str | None = None


def _extraer_datos_evento_perfil_actualizado(
    cuerpo: dict[str, Any],
) -> tuple[str, int | None]:
    """Acepta el evento directo (`{"datos": {"clienteId": ...}}` o `{"clienteId": ...}`)
    o un sobre push de Google Cloud Pub/Sub (`{"message": {"data": "<base64>"}}`)."""
    evento = cuerpo
    if isinstance(cuerpo.get("message"), dict):
        data_b64 = cuerpo["message"].get("data")
        if not isinstance(data_b64, str) or not data_b64.strip():
            raise ValueError("mensaje Pub/Sub sin data en base64")
        try:
            decodificado = base64.b64decode(data_b64).decode("utf-8")
            evento = json.loads(decodificado)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"data de Pub/Sub invalida: {error}") from error

    if not isinstance(evento, dict):
        raise ValueError("el evento debe ser un objeto JSON")

    datos = evento.get("datos") if isinstance(evento.get("datos"), dict) else evento
    cliente_id = datos.get("clienteId") or datos.get("cliente_id")
    if not isinstance(cliente_id, str) or not cliente_id.strip():
        raise ValueError("falta clienteId en el evento")

    perfil_version_raw = datos.get("perfilVersion", datos.get("perfil_version"))
    perfil_version: int | None = None
    if perfil_version_raw is not None:
        if isinstance(perfil_version_raw, bool) or not isinstance(
            perfil_version_raw, int
        ):
            raise ValueError("perfilVersion invalido")
        perfil_version = perfil_version_raw

    return cliente_id.strip(), perfil_version


def create_app(
    config: Configuracion | None = None,
    lector_perfiles: LectorPerfiles | None = None,
    servicio_oferta: ServicioOferta | None = None,
    cache_score: CacheScore | None = None,
) -> FastAPI:
    """`lector_perfiles`/`servicio_oferta`/`cache_score` permiten inyectar
    dobles de prueba (ver `tests/fakes.py`) sin levantar un replica set ni un
    Redis reales. En producción se omiten y `create_app` los construye desde
    `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_primaria: MongoClient | None = None
        cliente_secundaria: MongoClient | None = None
        cliente_redis: redis.Redis | None = None
        if lector_perfiles is not None:
            app.state.lector_perfiles = lector_perfiles
        else:
            cifrador = CifradorAesGcm.desde_entorno(
                cifrado_clave=config.cifrado_clave,
                kms_key_name=config.kms_key_name,
                cifrado_clave_envuelta=config.cifrado_clave_envuelta,
            )
            cliente_primaria = MongoClient(config.mongo_uri)
            cliente_secundaria = MongoClient(
                config.mongo_uri,
                serverSelectionTimeoutMS=config.max_time_ms,
                connectTimeoutMS=config.max_time_ms,
                socketTimeoutMS=config.max_time_ms,
            )
            app.state.lector_perfiles = LectorPerfilesMongo(
                cliente_primaria,
                cliente_secundaria,
                config.max_time_ms,
                cifrador=cifrador,
            )

        if cache_score is not None:
            app.state.cache_score = cache_score
        elif servicio_oferta is not None:
            app.state.cache_score = getattr(servicio_oferta, "_cache_score", None)
        else:
            cliente_redis = redis.Redis.from_url(config.redis_url)
            app.state.cache_score = CacheScore(
                cliente_redis, config.oferta_cache_ttl_segundos
            )

        if servicio_oferta is not None:
            app.state.servicio_oferta = servicio_oferta
        else:
            app.state.servicio_oferta = ServicioOferta(
                app.state.lector_perfiles, app.state.cache_score
            )

        try:
            yield
        finally:
            if cliente_primaria is not None:
                cliente_primaria.close()
            if cliente_secundaria is not None:
                cliente_secundaria.close()
            if cliente_redis is not None:
                cliente_redis.close()

    app = FastAPI(title="solventa-rating", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "rating", "estado": "ok"}

    @app.get("/perfiles/{cliente_id}", tags=["perfiles"])
    def obtener_perfil(cliente_id: str, operation_time: str = Query(...)) -> dict:
        """`operation_time`: el valor que RISK devolvió en `POST /perfiles`
        (D-02). Hoy se pasa directo en la petición (en producción lo
        reenviaría el BFF)."""
        try:
            perfil = app.state.lector_perfiles.leer(cliente_id, operation_time)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail=f"operation_time invalido: {error}"
            ) from error
        if perfil is None:
            raise HTTPException(status_code=404, detail="perfil no encontrado")
        return perfil

    @app.post("/ofertas", tags=["ofertas"])
    def generar_oferta(body: SolicitudOferta) -> dict:
        """Calcula la oferta (score + prima, T-W01-6 completo) sobre el
        perfil leído con el mismo mecanismo causal de `GET /perfiles`
        (reusa `LectorPerfiles`, no duplica esa lectura). Nunca un 5xx crudo
        por una solicitud mal formada: `operation_time`/`cobertura`
        inválidos responden 400, perfil inexistente responde 404
        (CA-W01-05)."""
        try:
            oferta, _score_de_cache = app.state.servicio_oferta.generar(
                body.cliente_id,
                body.operation_time,
                body.cobertura,
                body.plazo_meses,
                body.direccion_inmueble,
            )
        except DatosInsuficientes as error:
            # CA-W01-05: el BFF lo traduce a `ErrorOferta` con reintento.
            raise HTTPException(status_code=422, detail=DETALLE_DATOS_INSUFICIENTES) from error
        except (ValueError, InvalidOperation) as error:
            raise HTTPException(
                status_code=400, detail=f"solicitud invalida: {error}"
            ) from error
        if oferta is None:
            raise HTTPException(status_code=404, detail="perfil no encontrado")
        return oferta

    @app.post("/eventos/perfil-actualizado", tags=["eventos"])
    def recibir_perfil_actualizado(body: dict[str, Any]) -> dict[str, Any]:
        """Receptor del evento `perfil.actualizado` (directo o push de Pub/Sub,
        Bloque 8): invalida la caché del score de ese cliente en Redis."""
        try:
            cliente_id, perfil_version = _extraer_datos_evento_perfil_actualizado(body)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail=f"evento invalido: {error}"
            ) from error

        eliminadas = 0
        if getattr(app.state, "cache_score", None) is not None and hasattr(
            app.state.cache_score, "invalidar_cliente"
        ):
            eliminadas = app.state.cache_score.invalidar_cliente(
                cliente_id, perfil_version
            )
        elif hasattr(app.state.servicio_oferta, "invalidar_cache_cliente"):
            eliminadas = app.state.servicio_oferta.invalidar_cache_cliente(
                cliente_id, perfil_version
            )

        logger_eventos.info(
            "cache de score invalidada por perfil.actualizado cliente=%s "
            "perfil_version=%s claves_eliminadas=%d",
            id_opaco(cliente_id),
            perfil_version,
            eliminadas,
        )
        return {"estado": "ok", "claves_eliminadas": eliminadas}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()

