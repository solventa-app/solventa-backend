"""`enviar-push` (KAN-259, D1): servicio de push genérico y reutilizable. Dos puertas:

- `POST /v1/push`: valida y publica en el tema de Pub/Sub `notificaciones-push` (202). Para quien
  prefiere no hablar de Pub/Sub directamente; D10, solo cuentas de servicio internas.
- `POST /eventos`: la suscripción push de Pub/Sub entrega aquí cada solicitud y la función la
  envía por SNS. Acepta el sobre de Pub/Sub o el evento directo (mismo patrón que
  `rating/app/main.py` con `perfil.actualizado`), así que también sirve para pruebas y para quien
  llama directo sin pasar por Pub/Sub.

Sin arquitectura hexagonal (regla de arquitectura #3): no es el límite anticorrupción con
proveedores externos (ese es `acl-worker`); es la función que D1 aprobó como punto de contacto
dedicado con SNS, mucho más simple que un adaptador hexagonal completo."""

import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, ConfigDict, Field

from app.autenticacion_servicios import autenticacion_para, modo_desde_entorno
from app.bus_eventos import construir_bus_eventos
from app.clientes import ClienteAuthHttp, ErrorServicio
from app.configuracion import Configuracion
from app.esquema_evento import EventoInvalido, construir_evento, validar
from app.observabilidad import configurar_trazas
from app.plantillas import Plantillas
from app.servicio import ServicioEnviarPush
from app.sns import construir_enviador

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("enviar_push")

configurar_trazas("enviar-push", instrumentos=("httpx",))


class SolicitudPush(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    idempotencia: str = Field(min_length=1, max_length=100)
    usuario_id: str = Field(alias="usuarioId", min_length=1)
    plantilla: str = Field(min_length=1)
    variables: dict[str, str] | None = None
    ruta: str | None = Field(default=None, max_length=200)
    ttl_segundos: int | None = Field(default=None, alias="ttlSegundos", ge=1, le=604800)
    silenciosa: bool | None = None


def _decodificar_sobre_pubsub(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """Acepta el evento directo o el sobre push de Pub/Sub (`{"message": {"data": "<base64>"}}`),
    igual que `rating/app/main.py` con `perfil.actualizado`."""
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
    servicio: ServicioEnviarPush | None = None,
    bus: Any = None,
) -> FastAPI:
    """`servicio` y `bus` permiten inyectar dobles en las pruebas; si no, se construyen del
    entorno (AUTH real por `httpx`, SNS en modo stub o real según `config.sns_modo`)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cliente_http = None
        if servicio is None:
            auth_modo = config.autenticacion_servicios or modo_desde_entorno()
            cliente_http = httpx.AsyncClient(
                base_url=config.auth_url,
                auth=autenticacion_para(auth_modo, "auth"),
                timeout=10.0,
            )
            app.state.servicio = ServicioEnviarPush(
                ClienteAuthHttp(cliente_http), construir_enviador(config), Plantillas()
            )
        else:
            app.state.servicio = servicio
        app.state.bus = (
            bus
            if bus is not None
            else construir_bus_eventos(config.pubsub_tema_notificaciones_push)
        )
        try:
            yield
        finally:
            if cliente_http is not None:
                await cliente_http.aclose()

    app = FastAPI(title="solventa-enviar-push", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "enviar-push", "estado": "ok"}

    @app.post("/v1/push", tags=["push"], status_code=202)
    def solicitar_push(body: SolicitudPush) -> dict[str, str]:
        """D10: solo cuentas de servicio internas (Cloud Run IAM); sin chequeo aquí, es de infra."""
        evento = construir_evento(
            body.idempotencia,
            body.usuario_id,
            body.plantilla,
            body.variables,
            body.ruta,
            body.ttl_segundos,
            body.silenciosa,
        )
        try:
            validar(evento)
        except EventoInvalido as error:
            raise HTTPException(status_code=400, detail=error.detalle) from error
        app.state.bus.publicar(evento)
        return {"solicitudId": evento["eventId"]}

    @app.post("/eventos", tags=["push"])
    async def recibir_evento(body: dict[str, Any]) -> dict[str, str]:
        """Privada (IAM de Cloud Run / suscripción push de Pub/Sub). 400 = evento inválido, no se
        reintenta. 503 = AUTH o SNS no respondieron, Pub/Sub reintenta con su backoff. 200 = el
        evento se resolvió, sea que se envió o que se confirmó sin enviar (regla #4)."""
        try:
            evento = _decodificar_sobre_pubsub(body)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        try:
            validar(evento)
        except EventoInvalido as error:
            raise HTTPException(status_code=400, detail=error.detalle) from error
        try:
            resultado = await app.state.servicio.procesar(evento)
        except ErrorServicio as error:
            logger.warning("fallo transitorio procesando el push: %s", type(error).__name__)
            raise HTTPException(status_code=503, detail="reintentar") from error
        return {"resultado": resultado}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
