"""ONBOARDING: verificación de identidad del cliente nuevo en el móvil (HU-M01).

Único escritor de su base (regla 1). Servicio privado: lo llama el BFF (canal `app`) y Cloud Tasks.
Las operaciones de la app llevan el token de la sesión en `X-Onboarding-Token`; el BFF solo lo
reenvía. Las imágenes van directo de la app al bucket con URLs firmadas de `almacenamiento` y de
ahí al ACL Worker: nunca pasan por aquí."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from app.application.autenticacion_servicios import autenticacion_para
from app.application.bus_eventos import construir_bus
from app.application.cifrado import construir_cifrador
from app.application.clientes import ClienteAlmacenamiento, ClienteCuentas, ClienteIdentidad
from app.application.configuracion import Configuracion
from app.application.planificador import (
    PlanificadorCloudTasks,
    PlanificadorLocal,
    VerificacionPendiente,
)
from app.application.repositorio import RepositorioPostgres, construir_pool
from app.application.servicio_onboarding import ErrorOnboarding, Reglas, ServicioOnboarding
from app.observabilidad import configurar_trazas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("onboarding", instrumentos=("httpx", "psycopg"))

logger = logging.getLogger("onboarding")


class IniciarRequest(BaseModel):
    jurisdiccion: str = Field(min_length=2, max_length=2)
    tipo_documento: str = Field(min_length=2, max_length=4)


class IniciarResponse(BaseModel):
    onboarding_id: str
    token: str
    token_vence_en: datetime


class ConsentimientoRequest(BaseModel):
    consentimiento_id: str = Field(min_length=1, max_length=100)


class CuentaRequest(BaseModel):
    usuario_id: str = Field(min_length=1, max_length=100)


class SubidaRequest(BaseModel):
    paso: str = Field(min_length=1, max_length=16)
    tipo_contenido: str = Field(min_length=1, max_length=100)
    tamano_bytes: int = Field(gt=0)


class SubidaResponse(BaseModel):
    archivo_id: str
    url: str
    metodo: str
    encabezados: dict[str, str]
    vence_en: datetime


class ConfirmacionResponse(BaseModel):
    archivo_id: str
    estado: str


class OnboardingResponse(BaseModel):
    id: str
    estado: str
    # Un código (ILEGIBLE, MENOR_DE_EDAD, ...), nunca un dato.
    motivo: str | None = None
    intento: int
    intentos_restantes: int
    # Si la sesión ya tiene cuenta: el BFF no crea otra en AUTH (quedaría huérfana).
    tiene_cuenta: bool = False


class ErrorResponse(BaseModel):
    detail: str


Token = Annotated[str | None, Header(alias="X-Onboarding-Token")]


def create_app(
    config: Configuracion | None = None, servicio: ServicioOnboarding | None = None
) -> FastAPI:
    """`servicio` permite inyectar dobles de prueba sin PostgreSQL ni servicios reales
    (ver `tests/fakes.py`)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: ConnectionPool | None = None
        cerrar: list[Any] = []
        if servicio is not None:
            app.state.servicio = servicio
        else:
            pool = construir_pool(config.database_url)
            modo = config.autenticacion_servicios
            storage = ClienteAlmacenamiento(
                config.almacenamiento_url,
                config.timeout_s,
                autenticacion_para(modo, "almacenamiento"),
            )
            identidad = ClienteIdentidad(
                config.acl_url, config.timeout_s, autenticacion_para(modo, "acl-worker")
            )
            cuentas = ClienteCuentas(
                config.auth_url, config.timeout_s, autenticacion_para(modo, "auth")
            )
            cerrar += [storage, identidad, cuentas]
            if config.disparador == "externo":
                planificador: Any = PlanificadorCloudTasks(
                    config.gcp_proyecto,
                    config.gcp_region,
                    config.tareas_cola,
                    config.url_propia,
                    config.tareas_cuenta,
                )
            else:
                planificador = PlanificadorLocal()
            app.state.servicio = ServicioOnboarding(
                RepositorioPostgres(pool),
                storage,
                identidad,
                construir_cifrador(config),
                planificador,
                construir_bus(config.tema_verificado, config.tema_rechazado),
                Reglas(
                    config.cliente_id_clave,
                    config.edad_minima,
                    config.max_intentos,
                    config.vigencia_token_s,
                ),
                cuentas=cuentas,
            )
            if isinstance(planificador, PlanificadorLocal):
                planificador.conectar(app.state.servicio.verificar)
        try:
            yield
        finally:
            for cliente in cerrar:
                cliente.cerrar()
            if pool is not None:
                pool.close()

    app = FastAPI(title="solventa-onboarding", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ErrorOnboarding)
    async def _error(_: Request, error: ErrorOnboarding) -> JSONResponse:
        return JSONResponse(status_code=error.estado_http, content={"detail": error.codigo})

    def _servicio() -> ServicioOnboarding:
        return app.state.servicio

    errores = {
        401: {"model": ErrorResponse, "description": "TOKEN_INVALIDO"},
        404: {
            "model": ErrorResponse,
            "description": "SESION_NO_ENCONTRADA / ARCHIVO_NO_ENCONTRADO",
        },
        409: {"model": ErrorResponse, "description": "ESTADO_INVALIDO, SIN_CONSENTIMIENTO, ..."},
        422: {"model": ErrorResponse, "description": "la solicitud no es válida"},
        503: {"model": ErrorResponse, "description": "ALMACENAMIENTO_NO_DISPONIBLE"},
    }

    def _vista(sesion: dict[str, Any]) -> OnboardingResponse:
        return OnboardingResponse(**_servicio().vista(sesion))

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "onboarding", "estado": "ok"}

    @app.post("/onboarding", tags=["onboarding"], response_model=IniciarResponse, responses=errores)
    def iniciar(body: IniciarRequest) -> IniciarResponse:
        """Crea la sesión y su token (30 min). El token solo se devuelve aquí."""
        sesion, token = _servicio().iniciar(body.jurisdiccion.upper(), body.tipo_documento.upper())
        return IniciarResponse(
            onboarding_id=sesion["id"], token=token, token_vence_en=sesion["token_vence_en"]
        )

    @app.get(
        "/onboarding/{sesion_id}",
        tags=["onboarding"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def consultar(sesion_id: str, token: Token = None) -> OnboardingResponse:
        """Lee el estado guardado: con el proveedor caído responde igual (EC010)."""
        return _vista(_servicio().autenticar(sesion_id, token))

    @app.post(
        "/onboarding/{sesion_id}/consentimiento",
        tags=["onboarding"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def consentimiento(
        sesion_id: str, body: ConsentimientoRequest, token: Token = None
    ) -> OnboardingResponse:
        """El BFF ya registró el consentimiento `onboarding-biometrico` en AUTH; aquí se guarda
        su id, que el storage exige en cada subida."""
        servicio_actual = _servicio()
        sesion = servicio_actual.autenticar(sesion_id, token)
        return _vista(servicio_actual.registrar_consentimiento(sesion, body.consentimiento_id))

    @app.post(
        "/onboarding/{sesion_id}/subidas",
        tags=["onboarding"],
        response_model=SubidaResponse,
        responses=errores,
    )
    def subida(sesion_id: str, body: SubidaRequest, token: Token = None) -> SubidaResponse:
        """URL firmada para subir una pieza (`ANVERSO`, `REVERSO`, `ROSTRO`, `VIDEO`). La app
        hace `PUT url` con `encabezados` tal cual."""
        servicio_actual = _servicio()
        sesion = servicio_actual.autenticar(sesion_id, token)
        return SubidaResponse(
            **servicio_actual.solicitar_subida(
                sesion, body.paso.upper(), body.tipo_contenido, body.tamano_bytes
            )
        )

    @app.post(
        "/onboarding/{sesion_id}/subidas/{archivo_id}/confirmar",
        tags=["onboarding"],
        response_model=ConfirmacionResponse,
        responses=errores,
    )
    def confirmar(sesion_id: str, archivo_id: str, token: Token = None) -> ConfirmacionResponse:
        servicio_actual = _servicio()
        sesion = servicio_actual.autenticar(sesion_id, token)
        estado = servicio_actual.confirmar_subida(sesion, archivo_id)
        return ConfirmacionResponse(archivo_id=archivo_id, estado=estado)

    @app.post(
        "/onboarding/{sesion_id}/enviar",
        tags=["onboarding"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def enviar(sesion_id: str, token: Token = None) -> OnboardingResponse:
        """Con las cuatro piezas DISPONIBLE, agenda la verificación y responde EN_VERIFICACION."""
        servicio_actual = _servicio()
        return _vista(servicio_actual.enviar(servicio_actual.autenticar(sesion_id, token)))

    @app.post(
        "/onboarding/{sesion_id}/reintentar",
        tags=["onboarding"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def reintentar(sesion_id: str, token: Token = None) -> OnboardingResponse:
        servicio_actual = _servicio()
        return _vista(servicio_actual.reintentar(servicio_actual.autenticar(sesion_id, token)))

    @app.post(
        "/onboarding/{sesion_id}/cuenta",
        tags=["onboarding"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def cuenta(sesion_id: str, body: CuentaRequest, token: Token = None) -> OnboardingResponse:
        """Liga la cuenta que el BFF creó en AUTH (M01-05b) a la sesión."""
        servicio_actual = _servicio()
        sesion = servicio_actual.autenticar(sesion_id, token)
        return _vista(servicio_actual.vincular_cuenta(sesion, body.usuario_id))

    @app.get(
        "/usuarios/{usuario_id}/onboarding",
        tags=["onboarding"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def de_usuario(usuario_id: str) -> OnboardingResponse:
        """La sesión más reciente de una cuenta (`miVerificacion`). Sin token: el BFF ya validó la
        sesión del usuario y el servicio es privado."""
        return _vista(_servicio().de_usuario(usuario_id))

    @app.post(
        "/tareas/verificar/{sesion_id}",
        tags=["operacion"],
        response_model=OnboardingResponse,
        responses=errores,
    )
    def tarea_verificar(sesion_id: str) -> OnboardingResponse:
        """La dispara Cloud Tasks (privado, token OIDC). Un 503 hace que la reintente."""
        try:
            return _vista(_servicio().verificar(sesion_id))
        except VerificacionPendiente as error:
            raise HTTPException(status_code=503, detail="VERIFICACION_PENDIENTE") from error

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
