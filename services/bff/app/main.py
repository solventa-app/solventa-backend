"""BFF GraphQL de Solventa (web y móvil).

Un solo esquema (`app/esquema.py`, espejo del contrato) servido en dos rutas
por canal: `/graphql/web` y `/graphql/app`. El canal viaja en el contexto para
diferenciarlos en el futuro (resolutores o métricas por canal, ADR-01).

Además sirve la API REST de los socios de distribución en `/v1/...` (HU-W07, `app/socios/`), que
convive con `/v1/graphql/{canal}`."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from strawberry.fastapi import GraphQLRouter

from app.autenticacion_usuarios import (
    MENSAJE_SIN_PERMISO,
    MENSAJE_SIN_SESION,
    MENSAJE_TOKEN_INVALIDO,
    TokenInvalido,
    extraer_cabecera_autorizacion,
    leer_usuario,
)
from app.clientes import Clientes, ErrorServicio, construir_clientes, construir_sondas
from app.configuracion import Configuracion
from app.cookie_refresh import CABECERA_CSRF, NOMBRE_COOKIE_REFRESH
from app.esquema import schema
from app.idioma import idioma_de
from app.observabilidad import configurar_trazas
from app.salud import (
    MetricasMonitoring,
    MetricasPrometheus,
    ServicioSalud,
    SinMetricas,
    sonda_http,
)
from app.servicio_codigo_onboarding import ServicioCodigoOnboarding
from app.servicio_firma_pago import ErrorFlujoDatos, ServicioFirmaPago
from app.servicio_oferta import ServicioOferta
from app.servicio_restablecimiento import ServicioRestablecimiento
from app.servicio_suscripcion import MENSAJE_DOCUMENTO_NO_DISPONIBLE, ServicioSuscripcion
from app.sesiones_revocadas import SesionesRevocadasMemoria, SesionesRevocadasRedis
from app.socios.antifraude import AlmacenContadoresMemoria, AlmacenContadoresRedis, Antifraude
from app.socios.api import api_de, construir_router, cuenta_en_el_consumo, es_ruta_de_socios
from app.socios.autenticacion import VerificadorTokensSocio
from app.socios.errores import ErrorApiSocio, datos_invalidos, no_disponible, respuesta_de_error
from app.socios.idempotencia import AlmacenIdempotenciaMemoria, AlmacenIdempotenciaRedis
from app.socios.limite_tasa import AlmacenVentanasMemoria, AlmacenVentanasRedis, LimiteTasa
from app.socios.modo_prueba import AlmacenMemoria, AlmacenRedis, ModoPrueba

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("bff", instrumentos=("httpx",))

CANALES = ("web", "app", "movil")
# Ruta aparte del canal web para el flujo de suscripción (firma, pago y emisión). Sirve el mismo
# esquema y el mismo contexto que `web`, pero el gateway le da un plazo propio (5 s): esas
# operaciones tardan de 1,5 a 3,5 s (imagen de la firma, ACL, contrato cifrado, KMS) y a los
# 700 ms el gateway devolvía 504 con la firma ya registrada. El resto del canal web conserva el
# corte duro de 700 ms (HA-LAT-004).
RUTA_SUSCRIPCION = "web-suscripcion"

logger_socios = logging.getLogger("bff.socios")
# Tope del registro de consumo dentro de la petición del socio: si AUTH tarda más, la llamada no se
# cuenta (queda en `bff_socios_consumo_perdido_total`) en vez de comerse el plazo de 700 ms.
TIMEOUT_CONSUMO_SEGUNDOS = 0.15
CONSUMO_PERDIDO = Counter(
    "bff_socios_consumo_perdido_total",
    "Llamadas de socios que no se pudieron registrar en el consumo (sin etiquetas de socio).",
)


async def _siempre() -> bool:
    return True


def _salud_desde_config(config: Configuracion, clientes: Clientes) -> tuple[ServicioSalud, list]:
    """La salud real: `/health` de cada micro y la fuente de métricas de `METRICAS_FUENTE`."""
    sondas_http, abiertos = construir_sondas(config)
    sondas = {nombre: sonda_http(cliente) for nombre, cliente in sondas_http.items()}
    sondas["bff"] = _siempre  # quien calcula está vivo
    if config.metricas_fuente == "prometheus":
        prometheus = httpx.AsyncClient(base_url=config.prometheus_url, timeout=2.0)
        abiertos.append(prometheus)
        metricas = MetricasPrometheus(prometheus)
    elif config.metricas_fuente == "monitoring" and config.gcp_proyecto:
        metricas = MetricasMonitoring(config.gcp_proyecto)
    else:
        metricas = SinMetricas()
    return ServicioSalud(sondas, clientes.acl.circuitos, metricas), abiertos


def create_app(
    config: Configuracion | None = None,
    clientes: Clientes | None = None,
    salud: ServicioSalud | None = None,
) -> FastAPI:
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
        almacen_codigo = AlmacenRedis(config.redis_url) if clientes is None else AlmacenMemoria()
        if clientes is None:
            abiertos.append(almacen_codigo)
        app.state.servicio_codigo_onboarding = ServicioCodigoOnboarding(
            app.state.clientes, almacen_codigo, config.cliente_id_clave
        )
        sesiones_revocadas = (
            SesionesRevocadasRedis(config.redis_url)
            if clientes is None
            else SesionesRevocadasMemoria()
        )
        if clientes is None:
            abiertos.append(sesiones_revocadas)
        app.state.sesiones_revocadas = sesiones_revocadas
        app.state.servicio_restablecimiento = ServicioRestablecimiento(
            app.state.clientes, almacen_codigo, config.cliente_id_clave, config.url_front
        )
        # API de socios (HU-W07).
        app.state.cliente_id_clave = config.cliente_id_clave
        if app.state.clientes.socios is not None:
            app.state.verificador_socios = VerificadorTokensSocio(app.state.clientes.socios)
            # Modo prueba (W07-10): en Redis si el BFF lo tiene; si no (pruebas), en memoria.
            almacen = AlmacenRedis(config.redis_url) if clientes is None else AlmacenMemoria()
            if clientes is None:
                abiertos.append(almacen)
            app.state.modo_prueba = ModoPrueba(almacen, app.state.clientes.socios)
            # Idempotency-Key de las cotizaciones (v0.54): en Redis; en pruebas, en memoria.
            idempotencia = (
                AlmacenIdempotenciaRedis(config.redis_url)
                if clientes is None
                else AlmacenIdempotenciaMemoria()
            )
            if clientes is None:
                abiertos.append(idempotencia)
            app.state.idempotencia_socios = idempotencia
            # Antifraude (v0.62, HA-SEG-004): contadores en Redis; en pruebas, en memoria.
            contadores = (
                AlmacenContadoresRedis(config.redis_url)
                if clientes is None
                else AlmacenContadoresMemoria()
            )
            if clientes is None:
                abiertos.append(contadores)
            app.state.antifraude_socios = Antifraude(contadores)
            # Límite de tasa por credencial (KAN-217, v0.61): en Redis; en pruebas, en memoria.
            ventanas = (
                AlmacenVentanasRedis(config.redis_url)
                if clientes is None
                else AlmacenVentanasMemoria()
            )
            if clientes is None:
                abiertos.append(ventanas)
            app.state.limite_tasa_socios = LimiteTasa(
                ventanas,
                {
                    "PRODUCCION": config.limite_socio_rps_produccion,
                    "PRUEBAS": config.limite_socio_rps_pruebas,
                },
            )
        # Salud del sistema (HU-W07). Con dobles y sin `salud`, todos los micros responden.
        if salud is not None:
            app.state.salud = salud
        elif clientes is None:
            app.state.salud, abiertos_salud = _salud_desde_config(config, app.state.clientes)
            abiertos.extend(abiertos_salud)
        else:
            nombres = ("bff", "auth", "acl-worker", "risk", "rating", "payments", "policy",
                       "almacenamiento", "under", "consolidador-fuentes")  # fmt: skip
            app.state.salud = ServicioSalud(
                {n: _siempre for n in nombres}, app.state.clientes.acl.circuitos
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

    def _ip_de(request: Request) -> str | None:
        """Primera IP de `X-Forwarded-For` (el gateway) o la del socket; solo para el tope."""
        reenviada = request.headers.get("x-forwarded-for")
        if reenviada:
            return reenviada.split(",")[0].strip() or None
        return request.client.host if request.client else None

    def contexto_para(canal: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
        async def contexto(request: Request, response: Response) -> dict[str, Any]:
            # El token se lee aquí, una vez por petición; cada resolutor decide con
            # `exigir_rol` (app/esquema.py) si lo exige y qué roles admite.
            # Si viene detrás de GCP API Gateway (ESPv2), lee `X-Forwarded-Authorization`.
            usuario, error_token = None, None
            cabecera_auth = extraer_cabecera_autorizacion(request.headers)
            try:
                usuario = leer_usuario(cabecera_auth, config.jwt_secreto)
            except TokenInvalido as error:
                error_token = str(error)
            if usuario is not None and usuario.sid:
                if await app.state.sesiones_revocadas.esta_revocada(usuario.sid):
                    usuario, error_token = None, MENSAJE_TOKEN_INVALIDO
            return {
                "canal": canal,
                "clientes": app.state.clientes,
                "servicio_oferta": app.state.servicio_oferta,
                "servicio_firma_pago": app.state.servicio_firma_pago,
                "servicio_codigo_onboarding": app.state.servicio_codigo_onboarding,
                # «Olvidé mi contraseña» (KAN-26): el token del enlace viaja en esta cabecera.
                "servicio_restablecimiento": app.state.servicio_restablecimiento,
                "enlace_clave": request.headers.get("x-enlace-clave"),
                "ip_cliente": _ip_de(request),
                # Token del enlace de firma (W03-33): viaja en esta cabecera, nunca en la URL.
                "enlace_firma": request.headers.get("x-enlace-firma"),
                "usuario": usuario,
                "sesiones_revocadas": app.state.sesiones_revocadas,
                "error_token": error_token,
                # Idioma del selector del header (lo manda el frontend); ver app/idioma.py.
                "idioma": idioma_de(request.headers.get("accept-language")),
                # KAN-26: la moneda del selector de la web, para los documentos que se firman.
                "moneda": request.headers.get("x-moneda"),
                "modo_autenticacion": config.autenticacion_usuarios,
                "cliente_id_clave": config.cliente_id_clave,
                # Token del recorrido de onboarding (HU-M01): no es una sesión de usuario.
                "token_onboarding": request.headers.get("x-onboarding-token"),
                # Salud del sistema (HU-W07): `estadoSistema` del BACKOFFICE.
                "salud": getattr(app.state, "salud", None),
                # Sesión web (T22): la cookie del refresh se lee y se escribe solo en el BFF.
                "response": response,
                "cookie_refresh": request.cookies.get(NOMBRE_COOKIE_REFRESH),
                "cabecera_csrf": request.headers.get(CABECERA_CSRF),
                "origen": request.headers.get("origin"),
                "cookie_refresh_secure": config.cookie_refresh_secure,
                "origenes_web": config.origenes_web,
            }

        return contexto

    for canal in CANALES:
        app.include_router(
            GraphQLRouter(schema, context_getter=contexto_para(canal)), prefix=f"/graphql/{canal}"
        )
        # Versión explícita de la API (HA-MOD-003, W04-10): `/v1/graphql/{canal}` sirve el mismo
        # esquema y la ruta sin versión queda como alias de v1 (EC030: versiones que conviven).
        app.include_router(
            GraphQLRouter(schema, context_getter=contexto_para(canal)),
            prefix=f"/v1/graphql/{canal}",
        )

    app.include_router(
        GraphQLRouter(schema, context_getter=contexto_para("web")),
        prefix=f"/v1/graphql/{RUTA_SUSCRIPCION}",
    )

    # ---------- API de socios (HU-W07) ----------
    app.include_router(construir_router())

    @app.exception_handler(ErrorApiSocio)
    async def error_de_socio(request: Request, error: ErrorApiSocio) -> JSONResponse:
        return respuesta_de_error(error, idioma_de(request.headers.get("accept-language")))

    @app.exception_handler(RequestValidationError)
    async def validacion(request: Request, error: RequestValidationError) -> JSONResponse:
        # En la API de socios, un cuerpo inválido es `ErrorPublico` (DATOS_INVALIDOS), no el
        # detalle de FastAPI; el resto del BFF responde como siempre.
        if es_ruta_de_socios(request.url.path):
            # El token y su scope se revisan antes que el cuerpo (dependencia `requiere`), pero un
            # JSON que ni se puede leer falla antes de las dependencias y el socio aún no está
            # identificado: se identifica aquí para que el 422 cuente en su consumo (KAN-30). Sin
            # un token válido no hay a quién contarlo y la respuesta sigue siendo el 422.
            verificador = getattr(app.state, "verificador_socios", None)
            if verificador is not None and getattr(request.state, "socio", None) is None:
                try:
                    request.state.socio = await verificador.verificar(request.headers)
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
            idioma = idioma_de(request.headers.get("accept-language"))
            return respuesta_de_error(datos_invalidos(), idioma)
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def consumo_y_errores_de_socios(request: Request, siguiente):
        """Registra cada llamada de un socio (W07: consumo por API, sin datos personales) y
        convierte una falla inesperada en un 503 controlado (regla 4). El registro se espera
        **antes** de responder (KAN-30): una tarea suelta tras la respuesta se perdía en Cloud
        Run (`cpu_idle = true`). La latencia registrada no incluye el propio registro, que tiene
        su tope (`TIMEOUT_CONSUMO_SEGUNDOS`). Una respuesta repetida por `Idempotency-Key` no se
        vuelve a contar."""
        if not es_ruta_de_socios(request.url.path):
            return await siguiente(request)
        inicio = time.perf_counter()
        try:
            respuesta = await siguiente(request)
        except Exception:
            logger_socios.exception("falla inesperada en la API de socios")
            respuesta = respuesta_de_error(
                no_disponible(), idioma_de(request.headers.get("accept-language"))
            )
        socio = getattr(request.state, "socio", None)
        clientes_app = getattr(app.state, "clientes", None)
        socios = getattr(clientes_app, "socios", None)
        repetida = getattr(request.state, "consumo_omitido", False)
        api = api_de(request.url.path)
        antifraude = getattr(app.state, "antifraude_socios", None)
        if (
            socio is not None
            and antifraude is not None
            and respuesta.status_code in (403, 404)
            and not getattr(request.state, "suspendida", False)
        ):
            await antifraude.respuesta_rechazada(socio.id, socio.entorno)
        if socio is not None and socios is not None and not repetida and cuenta_en_el_consumo(api):
            codigo = respuesta.status_code
            resultado = "OK" if codigo < 400 else ("ERROR" if codigo >= 500 else "ERROR_CLIENTE")
            latencia = int((time.perf_counter() - inicio) * 1000)
            await _registrar_consumo(socios, socio, api, resultado, latencia)
        return respuesta

    @app.get("/polizas/{poliza_id}/documento", tags=["polizas"], response_class=Response)
    async def documento_poliza(poliza_id: str, request: Request) -> Response:
        """El PDF de la póliza (HU-W03, CA-W03-08). Es una ruta REST y no GraphQL porque entrega un
        archivo. Es un documento con datos personales: exige sesión SIEMPRE (aunque la autenticación
        de GraphQL esté en `opcional`), solo para CLIENTE y ASESOR, y un CLIENTE solo descarga el
        suyo; o el token del enlace de firma (`X-Enlace-Firma`), que sirve solo para la póliza de
        su propia propuesta ya emitida. Una póliza que no existe o que es de otro responde igual
        (404): no se revela cuál."""
        enlace = request.headers.get("x-enlace-firma")
        try:
            usuario = leer_usuario(
                extraer_cabecera_autorizacion(request.headers), config.jwt_secreto
            )
        except TokenInvalido as error:
            # Detrás de GCP API Gateway una petición sin sesión llega con el ID token del gateway
            # en `Authorization`: no es una sesión de Solventa. Con el token del enlace basta.
            if not enlace:
                return JSONResponse({"mensaje": str(error)}, status_code=401)
            usuario = None
        if usuario is None and enlace:
            # El titular que abrió el enlace de firma descarga la póliza que ese enlace emitió.
            servicio: ServicioFirmaPago = app.state.servicio_firma_pago
            autorizado = await servicio.autorizar_poliza_por_enlace(enlace, poliza_id)
            if isinstance(autorizado, ErrorFlujoDatos):
                return _respuesta_de_error(autorizado)
        elif usuario is None:
            return JSONResponse({"mensaje": MENSAJE_SIN_SESION}, status_code=401)
        elif usuario.rol not in ("CLIENTE", "ASESOR"):
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
        enlace = request.headers.get("x-enlace-firma")
        try:
            usuario = leer_usuario(
                extraer_cabecera_autorizacion(request.headers), config.jwt_secreto
            )
        except TokenInvalido as error:
            # Detrás de GCP API Gateway, una petición sin sesión llega con el ID token del
            # gateway en `Authorization` (y sin `X-Forwarded-Authorization`): no es una sesión
            # de Solventa. Con el token del enlace basta, como en GraphQL (guarda el error y sigue).
            if not enlace:
                return JSONResponse({"mensaje": str(error)}, status_code=401)
            usuario = None
        servicio: ServicioFirmaPago = app.state.servicio_firma_pago
        autorizado = await servicio.autorizar_enlace(usuario, enlace, propuesta_id)
        if isinstance(autorizado, ErrorFlujoDatos):
            return _respuesta_de_error(autorizado)
        # KAN-26: el borrador sale en el idioma y la moneda que tiene activos quien lo abre.
        presentacion = await servicio.presentacion(
            idioma_de(request.headers.get("accept-language")), request.headers.get("x-moneda")
        )
        pdf = await servicio.contrato_pdf(autorizado, presentacion)
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


async def _registrar_consumo(socios, socio, api: str, resultado: str, latencia_ms: int) -> None:
    try:
        await asyncio.wait_for(
            socios.registrar_consumo(socio.id, socio.entorno, api, resultado, latencia_ms),
            TIMEOUT_CONSUMO_SEGUNDOS,
        )
    except Exception:  # noqa: BLE001 - el consumo nunca hace fallar la llamada del socio
        CONSUMO_PERDIDO.inc()
        logger_socios.warning("no se pudo registrar el consumo de un socio api=%s", api)


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
