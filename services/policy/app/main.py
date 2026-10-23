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

from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from app.application.autenticacion_servicios import autenticacion_para
from app.application.cifrado import cifrador_desde_entorno
from app.application.cliente_rating import ClienteRating, RatingNoDisponible
from app.application.configuracion import Configuracion
from app.application.repositorio_propuestas import RepositorioPropuestasPostgres, construir_pool
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
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None


class ErrorResponse(BaseModel):
    detail: str


def _respuesta(propuesta: dict[str, Any]) -> PropuestaResponse:
    return PropuestaResponse(**propuesta)


def create_app(
    config: Configuracion | None = None,
    servicio_propuestas: ServicioPropuestas | None = None,
) -> FastAPI:
    """`servicio_propuestas` permite inyectar dobles de prueba sin PostgreSQL ni RATING reales (ver
    `tests/fakes.py`). En producción se omite y `create_app` lo construye desde `Configuracion`."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: ConnectionPool | None = None
        cliente_rating: ClienteRating | None = None
        if servicio_propuestas is not None:
            app.state.servicio_propuestas = servicio_propuestas
        else:
            pool = construir_pool(config.database_url)
            cliente_rating = ClienteRating(
                config.rating_url,
                config.rating_timeout_segundos,
                autenticacion_para(config.autenticacion_servicios, "rating"),
            )
            app.state.servicio_propuestas = ServicioPropuestas(
                RepositorioPropuestasPostgres(pool, cifrador_desde_entorno()), cliente_rating
            )
        try:
            yield
        finally:
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

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
