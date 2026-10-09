"""BFF GraphQL de Solventa (web y móvil).

Un solo esquema (`app/esquema.py`, espejo del contrato) servido en dos rutas
por canal: `/graphql/web` y `/graphql/app`. El canal viaja en el contexto para
diferenciarlos en el futuro (resolutores o métricas por canal, ADR-01)."""

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from strawberry.fastapi import GraphQLRouter

from app.clientes import Clientes, construir_clientes
from app.configuracion import Configuracion
from app.esquema import schema
from app.servicio_oferta import ServicioOferta

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CANALES = ("web", "app")


def create_app(config: Configuracion | None = None, clientes: Clientes | None = None) -> FastAPI:
    """`clientes` permite inyectar dobles de prueba (ver `tests/fakes.py`) sin
    levantar AUTH, ACL Worker, RISK ni RATING. En producción se omite y los
    clientes HTTP se abren en el lifespan (nunca al importar el módulo)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        abiertos: list[httpx.AsyncClient] = []
        if clientes is not None:
            app.state.clientes = clientes
        else:
            app.state.clientes, abiertos = construir_clientes(config)
        app.state.servicio_oferta = ServicioOferta(app.state.clientes, config.cliente_id_clave)
        try:
            yield
        finally:
            for cliente in abiertos:
                await cliente.aclose()

    app = FastAPI(title="solventa-bff", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "bff", "estado": "ok"}

    def contexto_para(canal: str) -> Callable[[], Coroutine[Any, Any, dict[str, Any]]]:
        async def contexto() -> dict[str, Any]:
            return {
                "canal": canal,
                "clientes": app.state.clientes,
                "servicio_oferta": app.state.servicio_oferta,
            }

        return contexto

    for canal in CANALES:
        app.include_router(
            GraphQLRouter(schema, context_getter=contexto_para(canal)), prefix=f"/graphql/{canal}"
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
