"""BFF GraphQL de Solventa (web y móvil).

Un solo esquema (`app/esquema.py`, espejo del contrato) servido en dos rutas
por canal: `/graphql/web` y `/graphql/app`. El canal viaja en el contexto para
diferenciarlos en el futuro (resolutores o métricas por canal, ADR-01)."""

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from strawberry.fastapi import GraphQLRouter

from app.autenticacion_usuarios import (
    MENSAJE_SIN_PERMISO,
    MENSAJE_SIN_SESION,
    TokenInvalido,
    extraer_cabecera_autorizacion,
    leer_usuario,
)
from app.clientes import Clientes, ErrorServicio, construir_clientes
from app.configuracion import Configuracion
from app.esquema import schema
from app.idioma import idioma_de
from app.observabilidad import configurar_trazas
from app.servicio_firma_pago import ErrorFlujoDatos, ServicioFirmaPago
from app.servicio_oferta import ServicioOferta
from app.servicio_suscripcion import MENSAJE_DOCUMENTO_NO_DISPONIBLE, ServicioSuscripcion

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("bff", instrumentos=("httpx",))

CANALES = ("web", "app", "movil")


def create_app(config: Configuracion | None = None, clientes: Clientes | None = None) -> FastAPI:
    """`clientes` permite inyectar dobles de prueba (ver `tests/fakes.py`) sin
    levantar AUTH, ACL Worker, RISK ni RATING. En producción se omite y los
    clientes HTTP se abren en el lifespan (nunca al importar el módulo)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        abiertos: list[Any] = []
        if clientes is not None:
            app.state.clientes = clientes
        else:
            app.state.clientes, abiertos = construir_clientes(config)
        app.state.servicio_oferta = ServicioOferta(app.state.clientes, config.cliente_id_clave)
        app.state.servicio_firma_pago = ServicioFirmaPago(
            app.state.clientes, config.avisos_url, config.url_front
        )
        try:
            yield
        finally:
            for cliente in abiertos:
                await cliente.aclose()

    app = FastAPI(title="solventa-bff", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "bff", "estado": "ok"}

    def contexto_para(canal: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
        async def contexto(request: Request) -> dict[str, Any]:
            # El token se lee aquí, una vez por petición; cada resolutor decide con
            # `exigir_rol` (app/esquema.py) si lo exige y qué roles admite.
            # Si viene detrás de GCP API Gateway (ESPv2), lee `X-Forwarded-Authorization`.
            usuario, error_token = None, None
            cabecera_auth = extraer_cabecera_autorizacion(request.headers)
            try:
                usuario = leer_usuario(cabecera_auth, config.jwt_secreto)
            except TokenInvalido as error:
                error_token = str(error)
            return {
                "canal": canal,
                "clientes": app.state.clientes,
                "servicio_oferta": app.state.servicio_oferta,
                "servicio_firma_pago": app.state.servicio_firma_pago,
                # Token del enlace de firma (W03-33): viaja en esta cabecera, nunca en la URL.
                "enlace_firma": request.headers.get("x-enlace-firma"),
                "usuario": usuario,
                "error_token": error_token,
                # Idioma del selector del header (lo manda el frontend); ver app/idioma.py.
                "idioma": idioma_de(request.headers.get("accept-language")),
                "modo_autenticacion": config.autenticacion_usuarios,
                "cliente_id_clave": config.cliente_id_clave,
                # Token del recorrido de onboarding (HU-M01): no es una sesión de usuario.
                "token_onboarding": request.headers.get("x-onboarding-token"),
            }

        return contexto

    for canal in CANALES:
        app.include_router(
            GraphQLRouter(schema, context_getter=contexto_para(canal)), prefix=f"/graphql/{canal}"
        )

    @app.get("/polizas/{poliza_id}/documento", tags=["polizas"], response_class=Response)
    async def documento_poliza(poliza_id: str, request: Request) -> Response:
        """El PDF de la póliza (HU-W03, CA-W03-08). Es una ruta REST y no GraphQL porque entrega un
        archivo. Es un documento con datos personales: exige sesión SIEMPRE (aunque la autenticación
        de GraphQL esté en `opcional`), solo para CLIENTE y ASESOR, y un CLIENTE solo descarga el
        suyo. Una póliza que no existe o que es de otro responde igual (404): no se revela cuál."""
        try:
            usuario = leer_usuario(
                extraer_cabecera_autorizacion(request.headers), config.jwt_secreto
            )
        except TokenInvalido as error:
            return JSONResponse({"mensaje": str(error)}, status_code=401)
        if usuario is None:
            return JSONResponse({"mensaje": MENSAJE_SIN_SESION}, status_code=401)
        if usuario.rol not in ("CLIENTE", "ASESOR"):
            return JSONResponse({"mensaje": MENSAJE_SIN_PERMISO}, status_code=403)
        try:
            documento = await ServicioSuscripcion(app.state.clientes).documento_poliza(
                poliza_id, usuario
            )
        except ErrorServicio:
            return JSONResponse(
                {"mensaje": MENSAJE_DOCUMENTO_NO_DISPONIBLE},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        if documento is None:
            return JSONResponse({"mensaje": "Póliza no encontrada."}, status_code=404)
        numero, pdf = documento
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="poliza-{numero}.pdf"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/propuestas/{propuesta_id}/contrato.pdf", tags=["propuestas"], response_class=Response
    )
    async def contrato_pdf(propuesta_id: str, request: Request) -> Response:
        """El contrato a firmar en PDF (W03-31), con la misma autorización doble de la página del
        enlace: `X-Enlace-Firma` o la sesión del CLIENTE titular. Sin datos personales. Una
        propuesta ajena o inexistente responde igual (404)."""
        try:
            usuario = leer_usuario(
                extraer_cabecera_autorizacion(request.headers), config.jwt_secreto
            )
        except TokenInvalido as error:
            return JSONResponse({"mensaje": str(error)}, status_code=401)
        servicio: ServicioFirmaPago = app.state.servicio_firma_pago
        autorizado = await servicio.autorizar_enlace(
            usuario, request.headers.get("x-enlace-firma"), propuesta_id
        )
        if isinstance(autorizado, ErrorFlujoDatos):
            return _respuesta_de_error(autorizado)
        pdf = await servicio.contrato_pdf(autorizado)
        if isinstance(pdf, ErrorFlujoDatos):
            return _respuesta_de_error(pdf)
        if pdf is None:
            return JSONResponse({"mensaje": "Propuesta no encontrada."}, status_code=404)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'inline; filename="contrato.pdf"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


def _respuesta_de_error(error: ErrorFlujoDatos) -> JSONResponse:
    """Una ruta REST no tiene uniones de error: el motivo se vuelve un estado HTTP."""
    if error.motivo in ("PROPUESTA_NO_ENCONTRADA", "PROPUESTA_NO_VALIDA"):
        estado = 404
    elif error.motivo == "ENLACE_VENCIDO":
        estado = 409
    else:
        estado = 503
    cabeceras = {"Retry-After": "5"} if estado == 503 else None
    return JSONResponse({"mensaje": error.mensaje}, status_code=estado, headers=cabeceras)


app = create_app()
