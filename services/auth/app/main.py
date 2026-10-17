"""AUTH: consentimiento, usuarios y sesiones.

- Consentimiento (T-W01-4): PostgreSQL, tabla append-only `consentimientos` (ver
  `app/consentimientos.py`).
- Usuarios y sesiones (HU-M02): PostgreSQL, base `auth` (ver `app/usuarios.py` y
  `app/sesiones.py`).

Estos endpoints son **internos**: solo los llama el BFF, que valida el JWT y el rol antes
(en Cloud Run AUTH es privado). Por eso AUTH no vuelve a autorizar aquí."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from argon2 import PasswordHasher
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from app.configuracion import Configuracion
from app.consentimientos import AlmacenConsentimientos, AlmacenConsentimientosPostgres
from app.identificadores import id_opaco
from app.sesiones import CredencialesInvalidas, ServicioSesiones
from app.usuarios import (
    DatoInvalido,
    RepositorioUsuariosPostgres,
    ServicioUsuarios,
    UsuarioDuplicado,
    construir_pool,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("auth.consentimientos")
logger_usuarios = logging.getLogger("auth.usuarios")

RolUsuario = Literal["CLIENTE", "ASESOR", "BACKOFFICE"]


class Actor(BaseModel):
    rol: RolUsuario
    usuario_id: str


class SolicitudConsentimiento(BaseModel):
    finalidad: str
    version_texto: str
    actor: Actor | None = None


class ConsentimientoResponse(BaseModel):
    id: str
    finalidad: str
    version_texto: str
    otorgado_en: str
    revocado_en: str | None
    vigente: bool
    actor: Actor | None = None


class Credenciales(BaseModel):
    usuario: str
    clave: str


class SolicitudUsuario(BaseModel):
    usuario: str
    clave: str
    rol: str


class SolicitudRol(BaseModel):
    rol: str


class UsuarioResponse(BaseModel):
    id: str
    usuario: str
    rol: str
    estado: str
    creado_en: datetime


class SesionResponse(BaseModel):
    token: str
    expira_en: datetime
    usuario: UsuarioResponse


def create_app(
    almacen: AlmacenConsentimientos | None = None,
    servicio_usuarios: ServicioUsuarios | None = None,
    servicio_sesiones: ServicioSesiones | None = None,
    config: Configuracion | None = None,
) -> FastAPI:
    """Los parámetros permiten inyectar dobles de prueba (ver `tests/fakes.py`) sin
    levantar PostgreSQL. Si se omiten, el lifespan arma todo desde `Configuracion`
    (un solo pool para consentimientos, usuarios y sesiones)."""
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool: ConnectionPool | None = None
        servicios_inyectados = servicio_usuarios is not None and servicio_sesiones is not None
        if almacen is None or not servicios_inyectados:
            pool = construir_pool(config.database_url)
        if almacen is None:
            app.state.consentimientos = AlmacenConsentimientosPostgres(pool)
        if servicios_inyectados:
            app.state.usuarios = servicio_usuarios
            app.state.sesiones = servicio_sesiones
        else:
            repositorio = RepositorioUsuariosPostgres(pool)
            hasher = PasswordHasher()
            app.state.usuarios = ServicioUsuarios(repositorio, hasher)
            app.state.sesiones = ServicioSesiones(
                repositorio, hasher, config.jwt_secreto, config.jwt_vigencia_minutos
            )
        try:
            yield
        finally:
            if pool is not None:
                pool.close()

    app = FastAPI(title="solventa-auth", version="0.1.0", lifespan=lifespan)
    if almacen is not None:
        # Un doble inyectado sirve aunque el lifespan no corra (TestClient sin `with`).
        app.state.consentimientos = almacen

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "auth", "estado": "ok"}

    # ---------- Consentimiento (append-only en PostgreSQL) ----------

    @app.post("/consentimientos", tags=["consentimientos"], response_model=ConsentimientoResponse)
    def registrar(body: SolicitudConsentimiento) -> ConsentimientoResponse:
        actor = body.actor.model_dump() if body.actor else None
        consentimiento = app.state.consentimientos.registrar(
            body.finalidad, body.version_texto, actor
        )
        logger.info("consentimiento registrado id=%s", id_opaco(consentimiento.id))
        return ConsentimientoResponse(**consentimiento.como_dict())

    @app.get(
        "/consentimientos/{consentimiento_id}",
        tags=["consentimientos"],
        response_model=ConsentimientoResponse,
    )
    def obtener(consentimiento_id: str) -> ConsentimientoResponse:
        consentimiento = app.state.consentimientos.obtener(consentimiento_id)
        if consentimiento is None:
            raise HTTPException(status_code=404, detail="consentimiento no encontrado")
        return ConsentimientoResponse(**consentimiento.como_dict())

    @app.post(
        "/consentimientos/{consentimiento_id}/revocar",
        tags=["consentimientos"],
        response_model=ConsentimientoResponse,
    )
    def revocar(consentimiento_id: str) -> ConsentimientoResponse:
        consentimiento = app.state.consentimientos.revocar(consentimiento_id)
        if consentimiento is None:
            raise HTTPException(status_code=404, detail="consentimiento no encontrado")
        logger.info("consentimiento revocado id=%s", id_opaco(consentimiento.id))
        return ConsentimientoResponse(**consentimiento.como_dict())

    # ---------- Sesiones ----------

    @app.post("/sesiones", tags=["sesiones"], response_model=SesionResponse)
    def iniciar_sesion(body: Credenciales) -> SesionResponse:
        try:
            sesion = app.state.sesiones.iniciar_sesion(body.usuario, body.clave)
        except CredencialesInvalidas as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        return SesionResponse(**sesion)

    # ---------- Usuarios ----------

    @app.post("/usuarios", tags=["usuarios"], response_model=UsuarioResponse)
    def crear_usuario(body: SolicitudUsuario) -> UsuarioResponse:
        try:
            usuario = app.state.usuarios.crear(body.usuario, body.clave, body.rol)
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except UsuarioDuplicado as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        logger_usuarios.info("usuario creado id=%s rol=%s", id_opaco(usuario["id"]), usuario["rol"])
        return UsuarioResponse(**usuario)

    @app.get("/usuarios", tags=["usuarios"], response_model=list[UsuarioResponse])
    def listar_usuarios(rol: str | None = None) -> list[UsuarioResponse]:
        try:
            return [UsuarioResponse(**u) for u in app.state.usuarios.listar(rol)]
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/usuarios/{usuario_id}", tags=["usuarios"], response_model=UsuarioResponse)
    def obtener_usuario(usuario_id: str) -> UsuarioResponse:
        usuario = app.state.usuarios.obtener(usuario_id)
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        return UsuarioResponse(**usuario)

    @app.post("/usuarios/{usuario_id}/rol", tags=["usuarios"], response_model=UsuarioResponse)
    def cambiar_rol(usuario_id: str, body: SolicitudRol) -> UsuarioResponse:
        try:
            usuario = app.state.usuarios.cambiar_rol(usuario_id, body.rol)
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        logger_usuarios.info("rol cambiado id=%s rol=%s", id_opaco(usuario_id), usuario["rol"])
        return UsuarioResponse(**usuario)

    @app.post("/usuarios/{usuario_id}/suspender", tags=["usuarios"], response_model=UsuarioResponse)
    def suspender(usuario_id: str) -> UsuarioResponse:
        usuario = app.state.usuarios.suspender(usuario_id)
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        logger_usuarios.info("usuario suspendido id=%s", id_opaco(usuario_id))
        return UsuarioResponse(**usuario)

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
