"""Avisos en vivo (SSE) de Solventa (HU-W03, W03-37).

`EventSource` no manda cabeceras, así que el front pide al BFF un ticket (`ticketAvisos`: de un solo
uso, 60 s, en Redis) y abre `GET /avisos/stream?ticket=…`. Este servicio consume el ticket, revisa
el `Origin` y reenvía lo que POLICY publica en Redis para ese rol. No tiene estado ni base de datos:
si Redis cae, la conexión se corta y la pantalla vuelve a consultar al reconectar.

Cloud Run con ingreso público, **fuera del API Gateway** (un stream largo no cabe en el gateway)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.canales import canales_de
from app.configuracion import Configuracion
from app.observabilidad import configurar_trazas
from app.stream import reenviar
from app.tickets import TicketInvalido, consumir_ticket

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("avisos")

configurar_trazas("avisos")


def create_app(config: Configuracion | None = None, cliente_redis: Any = None) -> FastAPI:
    """`cliente_redis` permite inyectar un doble en las pruebas; si no, se abre en el lifespan."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        abierto = None
        if cliente_redis is None:
            from redis.asyncio import Redis

            abierto = app.state.redis = Redis.from_url(config.redis_url)
        try:
            yield
        finally:
            if abierto is not None:
                await abierto.aclose()

    app = FastAPI(title="solventa-avisos", version="0.1.0", lifespan=lifespan)
    app.state.redis = cliente_redis

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "avisos", "estado": "ok"}

    @app.get("/avisos/stream", tags=["avisos"])
    async def stream(request: Request, ticket: str = "") -> Response:
        """Un stream `text/event-stream` por pestaña. 403 si el `Origin` no es del front; 401 si el
        ticket no existe, venció o ya se usó; 503 si Redis no responde."""
        origen = (request.headers.get("origin") or "").rstrip("/")
        # `EventSource` entre orígenes siempre manda `Origin`; sin él no es el front.
        if origen not in config.origenes_permitidos:
            return JSONResponse({"detail": "ORIGEN_NO_PERMITIDO"}, status_code=403)
        try:
            datos = await consumir_ticket(app.state.redis, ticket)
        except TicketInvalido:
            return JSONResponse({"detail": "TICKET_INVALIDO"}, status_code=401)
        except Exception as error:
            logger.warning(
                "Redis no disponible al consumir el ticket error=%s", type(error).__name__
            )
            return JSONResponse(
                {"detail": "AVISOS_NO_DISPONIBLES"}, status_code=503, headers={"Retry-After": "5"}
            )
        canales = canales_de(datos)
        pubsub = app.state.redis.pubsub()
        try:
            if canales:
                await pubsub.subscribe(*canales)
        except Exception as error:
            logger.warning("no se pudo suscribir error=%s", type(error).__name__)
            await _cerrar(pubsub)
            return JSONResponse(
                {"detail": "AVISOS_NO_DISPONIBLES"}, status_code=503, headers={"Retry-After": "5"}
            )
        logger.info("stream abierto rol=%s canales=%d", datos.get("rol"), len(canales))

        async def cuerpo() -> AsyncIterator[str]:
            try:
                async for fragmento in reenviar(pubsub, config.latido_segundos):
                    yield fragmento
            finally:
                await _cerrar(pubsub)

        return StreamingResponse(
            cuerpo(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": origen,
                "Vary": "Origin",
            },
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


async def _cerrar(pubsub: Any) -> None:
    try:
        await pubsub.aclose()
    except Exception:
        logger.warning("no se pudo cerrar la suscripción")


app = create_app()
