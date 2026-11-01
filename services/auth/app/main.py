"""AUTH: consentimiento, usuarios y sesiones.

- Consentimiento (T-W01-4): PostgreSQL, tabla append-only `consentimientos` (ver
  `app/consentimientos.py`).
- Usuarios y sesiones (HU-M02): PostgreSQL, base `auth` (ver `app/usuarios.py` y
  `app/sesiones.py`).
- Socios de distribución (HU-W07): credenciales, tokens ES256 y su JWKS, presupuesto de cotizaciones
  y consumo (ver `app/socios.py` y `app/tokens_socio.py`).

Estos endpoints son **internos**: solo los llama el BFF, que valida el JWT y el rol antes
(en Cloud Run AUTH es privado). Por eso AUTH no vuelve a autorizar aquí."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Literal

from argon2 import PasswordHasher
from fastapi import FastAPI, HTTPException, Response
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from app.cifrado import construir_cifrador
from app.configuracion import Configuracion
from app.consentimientos import AlmacenConsentimientos, AlmacenConsentimientosPostgres
from app.identificadores import cliente_id_desde_documento, id_opaco
from app.observabilidad import configurar_trazas
from app.refrescos import RepositorioRefrescosPostgres
from app.sesiones import (
    CredencialesInvalidas,
    RefreshInvalido,
    ServicioSesiones,
    VerificacionPendiente,
)
from app.socios import (
    CredencialesSocioInvalidas,
    DatoSocioInvalido,
    PresupuestoAgotado,
    RepositorioSociosPostgres,
    ServicioSocios,
)
from app.tokens_socio import FirmadorTokensSocio
from app.usuarios import (
    DatoInvalido,
    RepositorioUsuariosPostgres,
    ServicioUsuarios,
    UsuarioDuplicado,
    construir_pool,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

configurar_trazas("auth", instrumentos=("httpx", "psycopg"))
logger = logging.getLogger("auth.consentimientos")
logger_usuarios = logging.getLogger("auth.usuarios")

# SOCIO (HU-W07, W07-20): el socio de distribución otorga el consentimiento en nombre del titular, y
# entonces `usuario_id` es el id del socio.
RolUsuario = Literal["CLIENTE", "ASESOR", "BACKOFFICE", "SOCIO"]


class Actor(BaseModel):
    rol: RolUsuario
    usuario_id: str


class SolicitudConsentimiento(BaseModel):
    finalidad: str
    version_texto: str
    actor: Actor | None = None
    titular_id: str | None = None
    documento_identidad: str | None = None
    tipo_dato: str | None = None
    fuente_datos: str | None = None
    canal: str | None = None


class ConsentimientoResponse(BaseModel):
    id: str
    finalidad: str
    version_texto: str
    otorgado_en: str
    revocado_en: str | None
    vence_en: str
    vigente: bool
    estado: str
    actor: Actor | None = None
    titular_id: str | None = None
    tipo_dato: str | None = None
    fuente_datos: str | None = None
    canal: str | None = None


class Credenciales(BaseModel):
    # El alias, o el correo de una cuenta del onboarding (HU-M01).
    usuario: str
    clave: str
    # Canal desde el que se inicia sesión (`app` = móvil, `web`). Sin canal se trata como `web`.
    canal: str | None = None


class SolicitudCorreo(BaseModel):
    correo: str


class SolicitudCuentaOnboarding(BaseModel):
    correo: str
    clave: str


class SolicitudCierreVerificacion(BaseModel):
    aprobada: bool
    # Leídos del documento por el onboarding; solo con `aprobada`. Se guardan cifrados, y el
    # documento fija además el `titular_id` del CLIENTE (W03-36).
    nombre: str | None = None
    documento: str | None = None


class SolicitudRenovacion(BaseModel):
    usuario_id: str
    expira_en: datetime


class SolicitudUsuario(BaseModel):
    usuario: str
    clave: str
    rol: str
    nombre: str | None = None  # cualquier rol; se guarda cifrado y no se devuelve al crear
    # Solo CLIENTE (HU-W03, bloque 8); se guardan cifrados y no se devuelven al crear.
    correo: str | None = None
    telefono: str | None = None
    # Solo CLIENTE y obligatorio para él, junto con el correo (W03-27). Se guarda cifrado y como
    # titular verificado; nunca se devuelve.
    documento_identidad: str | None = None


class SolicitudContacto(BaseModel):
    """Lo que no llega se conserva; al menos uno es obligatorio."""

    correo: str | None = None
    telefono: str | None = None


class ContactoResponse(BaseModel):
    """Dato personal en claro: solo lo pide POLICY (servicio a servicio) y nunca se loguea."""

    correo: str | None
    telefono: str | None


class IdentidadResponse(BaseModel):
    """Dato personal en claro: solo lo pide el BFF con el id del token y nunca se loguea."""

    titular_id: str
    documento: str


class SolicitudNombres(BaseModel):
    """Los ids van en el cuerpo, nunca en la URL (regla #6)."""

    ids: list[str]


class SolicitudRol(BaseModel):
    rol: str


class UsuarioResponse(BaseModel):
    id: str
    usuario: str
    rol: str
    estado: str
    creado_en: datetime
    # Solo lo llenan `POST /sesiones` y `GET /usuarios/{id}` (el propio usuario); en listas,
    # altas y cambios va en null para no exponer nombres ajenos.
    nombre: str | None = None
    # Preferencias de la interfaz (KAN-209); la web las aplica al iniciar sesión.
    idioma: str = "ES"
    moneda: str = "COP"
    # Solo dice si tiene contacto (para que la web sepa cuándo pedirlo), nunca cuál es.
    tiene_contacto: bool = False
    # Como `nombre`: solo en `POST /sesiones` y `GET /usuarios/{id}` (W03-34, `•••• 5678`).
    documento_enmascarado: str | None = None
    # v0.24: el correo propio, como `nombre`: solo en `POST /sesiones` y `GET /usuarios/{id}`.
    correo: str | None = None


class SolicitudPreferencias(BaseModel):
    idioma: str
    moneda: str


class SesionResponse(BaseModel):
    token: str
    expira_en: datetime
    usuario: UsuarioResponse
    # T22: el refresh opaco y su vencimiento. Null si AUTH no tiene refrescos configurados.
    refresh_token: str | None = None
    refresh_expira_en: datetime | None = None


class SolicitudRefresh(BaseModel):
    refresh_token: str
    canal: str | None = None


class SolicitudCierre(BaseModel):
    refresh_token: str


# ---------- Socios (HU-W07) ----------

Entorno = Literal["PRODUCCION", "PRUEBAS"]


class SolicitudTokenSocio(BaseModel):
    client_id: str
    clave: str


class TokenSocioResponse(BaseModel):
    token: str
    expira_en: datetime
    entorno: Entorno
    socio_id: str


class SocioResponse(BaseModel):
    id: str
    nombre: str
    estado: str
    tasa_comision: str  # decimal como texto, p. ej. "0.1000"


class CredencialSocioResponse(BaseModel):
    """La clave en claro: solo la pide el BFF para el portal del propio socio. Nunca se loguea."""

    entorno: Entorno
    client_id: str
    clave: str
    creada_en: datetime


class PresupuestoResponse(BaseModel):
    restantes: int


class SolicitudConsumoSocio(BaseModel):
    entorno: Entorno
    api: str
    resultado: Literal["OK", "ERROR_CLIENTE", "ERROR"]
    latencia_ms: int


class ConsumoApiResponse(BaseModel):
    api: str
    llamadas: int
    errores: int
    latencia_media_ms: int | None = None


class ConsumoDiaResponse(BaseModel):
    fecha: date
    llamadas: int


class PresupuestoConsumoResponse(BaseModel):
    asignadas: int
    consumidas: int
    restantes: int


class ConsumoSocioResponse(BaseModel):
    entorno: Entorno
    desde: date
    hasta: date
    presupuesto: PresupuestoConsumoResponse
    por_api: list[ConsumoApiResponse]
    diario: list[ConsumoDiaResponse]


def create_app(
    almacen: AlmacenConsentimientos | None = None,
    servicio_usuarios: ServicioUsuarios | None = None,
    servicio_sesiones: ServicioSesiones | None = None,
    config: Configuracion | None = None,
    servicio_socios: ServicioSocios | None = None,
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
            cifrador = construir_cifrador(config)
            app.state.usuarios = ServicioUsuarios(
                repositorio, hasher, cifrador, config.cliente_id_clave
            )
            app.state.sesiones = ServicioSesiones(
                repositorio,
                hasher,
                config.jwt_secreto,
                config.jwt_vigencia_minutos,
                clave_correo=config.cliente_id_clave,
                refrescos=RepositorioRefrescosPostgres(pool),
                refresh_dias=config.refresh_vigencia_dias,
            )
        # Con todo lo demás inyectado (pruebas) no hay pool: las de socios inyectan el suyo.
        if servicio_socios is None and pool is not None:
            app.state.socios = ServicioSocios(
                RepositorioSociosPostgres(pool),
                construir_cifrador(config),
                FirmadorTokensSocio(config.socios_clave_firma),
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
    if servicio_socios is not None:
        app.state.socios = servicio_socios

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "auth", "estado": "ok"}

    # ---------- Consentimiento (append-only en PostgreSQL) ----------

    @app.post("/consentimientos", tags=["consentimientos"], response_model=ConsentimientoResponse)
    def registrar(body: SolicitudConsentimiento) -> ConsentimientoResponse:
        actor = body.actor.model_dump() if body.actor else None
        titular_id = body.titular_id
        if not titular_id and body.documento_identidad and body.documento_identidad.strip():
            titular_id = cliente_id_desde_documento(
                body.documento_identidad, config.cliente_id_clave
            )
        consentimiento = app.state.consentimientos.registrar(
            body.finalidad,
            body.version_texto,
            actor,
            titular_id=titular_id,
            tipo_dato=body.tipo_dato,
            fuente_datos=body.fuente_datos,
            canal=body.canal,
            vigencia_dias=config.consentimiento_vigencia_dias,
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
            sesion = app.state.sesiones.iniciar_sesion(body.usuario, body.clave, body.canal)
        except CredencialesInvalidas as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except VerificacionPendiente as error:
            raise HTTPException(status_code=403, detail="VERIFICACION_PENDIENTE") from error
        usuario_id = sesion["usuario"]["id"]
        sesion["usuario"]["nombre"] = app.state.usuarios.nombre_de(usuario_id)
        sesion["usuario"]["documento_enmascarado"] = app.state.usuarios.documento_enmascarado_de(
            usuario_id
        )
        sesion["usuario"]["correo"] = app.state.usuarios.correo_de(usuario_id)
        return SesionResponse(**sesion)

    @app.post("/sesiones/renovar", tags=["sesiones"], response_model=SesionResponse)
    def renovar_permisos(body: SolicitudRenovacion) -> SesionResponse:
        """`actualizarSesion` (HU-M01): un token nuevo con los scopes del estado actual y la misma
        expiración. Solo lo llama el BFF con el id y la expiración de un token que ya validó."""
        try:
            sesion = app.state.sesiones.renovar_permisos(body.usuario_id, body.expira_en)
        except CredencialesInvalidas as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        sesion["usuario"]["nombre"] = app.state.usuarios.nombre_de(body.usuario_id)
        sesion["usuario"]["documento_enmascarado"] = app.state.usuarios.documento_enmascarado_de(
            body.usuario_id
        )
        sesion["usuario"]["correo"] = app.state.usuarios.correo_de(body.usuario_id)
        return SesionResponse(**sesion)

    @app.post("/sesiones/refresh", tags=["sesiones"], response_model=SesionResponse)
    def renovar_con_refresh(body: SolicitudRefresh) -> SesionResponse:
        """T22: cambia un refresh por un par nuevo. Mismo 401 genérico en cualquier fallo."""
        try:
            sesion = app.state.sesiones.renovar_con_refresh(body.refresh_token, body.canal)
        except RefreshInvalido as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        usuario_id = sesion["usuario"]["id"]
        sesion["usuario"]["nombre"] = app.state.usuarios.nombre_de(usuario_id)
        sesion["usuario"]["documento_enmascarado"] = app.state.usuarios.documento_enmascarado_de(
            usuario_id
        )
        return SesionResponse(**sesion)

    @app.post("/sesiones/cerrar", tags=["sesiones"], status_code=204)
    def cerrar_sesion(body: SolicitudCierre) -> None:
        """T22: revoca la cadena del refresh. Idempotente: no dice si el refresh existía."""
        app.state.sesiones.cerrar_sesion(body.refresh_token)

    # ---------- Usuarios ----------

    @app.post("/usuarios", tags=["usuarios"], response_model=UsuarioResponse)
    def crear_usuario(body: SolicitudUsuario) -> UsuarioResponse:
        try:
            usuario = app.state.usuarios.crear(
                body.usuario,
                body.clave,
                body.rol,
                body.nombre,
                body.correo,
                body.telefono,
                body.documento_identidad,
            )
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except UsuarioDuplicado as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        logger_usuarios.info("usuario creado id=%s rol=%s", id_opaco(usuario["id"]), usuario["rol"])
        return UsuarioResponse(**usuario)

    @app.post("/usuarios/onboarding/correo-registrado", tags=["usuarios"])
    def correo_registrado(body: SolicitudCorreo) -> dict[str, bool]:
        """¿Existe ya una cuenta con ese correo? La llama el BFF antes de enviar el código OTP del
        registro (HU-M01). El correo llega solo en el cuerpo y no se loguea."""
        try:
            return {"registrado": app.state.usuarios.correo_registrado(body.correo)}
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/usuarios/onboarding", tags=["usuarios"], response_model=UsuarioResponse)
    def crear_cuenta_onboarding(body: SolicitudCuentaOnboarding) -> UsuarioResponse:
        """Cuenta del onboarding del móvil (HU-M01, M01-05b): CLIENTE en PENDIENTE_VERIFICACION
        con correo y clave. La llama el BFF; el correo se guarda cifrado y nunca se loguea."""
        try:
            usuario = app.state.usuarios.crear_cuenta_onboarding(body.correo, body.clave)
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except UsuarioDuplicado as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        logger_usuarios.info("cuenta de onboarding creada id=%s", id_opaco(usuario["id"]))
        return UsuarioResponse(**usuario)

    @app.post(
        "/usuarios/{usuario_id}/verificacion", tags=["usuarios"], response_model=UsuarioResponse
    )
    def cerrar_verificacion(usuario_id: str, body: SolicitudCierreVerificacion) -> UsuarioResponse:
        """Resultado del onboarding (HU-M01): ACTIVO o RECHAZADA. Solo lo llama el servicio
        `onboarding` (AUTH es privado y el BFF no expone esta ruta). Idempotente. 404 si la cuenta
        no existe o ya no está pendiente."""
        try:
            usuario = app.state.usuarios.cerrar_verificacion(
                usuario_id, body.aprobada, body.nombre, body.documento
            )
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except UsuarioDuplicado as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        logger_usuarios.info(
            "verificación cerrada id=%s estado=%s", id_opaco(usuario_id), usuario["estado"]
        )
        return UsuarioResponse(**usuario)

    @app.post(
        "/usuarios/nombres",
        tags=["usuarios"],
        response_model=dict[str, str],
        responses={400: {"description": "demasiados ids"}},
    )
    def nombres_de_clientes(body: SolicitudNombres) -> dict[str, str]:
        """Nombres en claro de los CLIENTE pedidos (`{id: nombre}`); los que no tienen nombre
        no aparecen. Es dato personal: solo lo llama el BFF para un BACKOFFICE; nunca se loguea."""
        try:
            return app.state.usuarios.nombres(body.ids)
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/usuarios", tags=["usuarios"], response_model=list[UsuarioResponse])
    def listar_usuarios(rol: str | None = None) -> list[UsuarioResponse]:
        try:
            return [UsuarioResponse(**u) for u in app.state.usuarios.listar(rol)]
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/usuarios/{usuario_id}", tags=["usuarios"], response_model=UsuarioResponse)
    def obtener_usuario(usuario_id: str) -> UsuarioResponse:
        """Incluye el nombre: el BFF solo lo llama con el id del propio usuario (`yo`)."""
        usuario = app.state.usuarios.obtener(usuario_id)
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        return UsuarioResponse(
            **usuario,
            nombre=app.state.usuarios.nombre_de(usuario_id),
            documento_enmascarado=app.state.usuarios.documento_enmascarado_de(usuario_id),
            correo=app.state.usuarios.correo_de(usuario_id),
        )

    @app.get(
        "/usuarios/{usuario_id}/identidad", tags=["usuarios"], response_model=IdentidadResponse
    )
    def obtener_identidad(usuario_id: str) -> IdentidadResponse:
        """El titular y el documento en claro de un CLIENTE verificado (W03-33 y W03-34). Dato
        personal: solo lo llama el BFF con el id del propio usuario, y lo usa en memoria. 404 si no
        existe, no es CLIENTE o no tiene la identidad verificada. Nunca se loguea."""
        identidad = app.state.usuarios.identidad_de(usuario_id)
        if identidad is None:
            raise HTTPException(status_code=404, detail="identidad no encontrada")
        return IdentidadResponse(**identidad)

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
        app.state.sesiones.revocar_de_usuario(usuario_id)  # T22: sus refresh dejan de servir
        logger_usuarios.info("usuario suspendido id=%s", id_opaco(usuario_id))
        return UsuarioResponse(**usuario)

    @app.post(
        "/usuarios/{usuario_id}/preferencias", tags=["usuarios"], response_model=UsuarioResponse
    )
    def actualizar_preferencias(usuario_id: str, body: SolicitudPreferencias) -> UsuarioResponse:
        """Idioma y moneda por defecto. El BFF solo lo llama con el id del propio usuario."""
        try:
            usuario = app.state.usuarios.actualizar_preferencias(
                usuario_id, body.idioma, body.moneda
            )
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        logger_usuarios.info("preferencias actualizadas id=%s", id_opaco(usuario_id))
        return UsuarioResponse(**usuario)

    @app.post("/usuarios/{usuario_id}/contacto", tags=["usuarios"], response_model=UsuarioResponse)
    def actualizar_contacto(usuario_id: str, body: SolicitudContacto) -> UsuarioResponse:
        """Correo y teléfono del cliente. El BFF solo lo llama con el id del propio usuario.
        Se guardan cifrados y la respuesta solo dice que ya hay contacto, no cuál."""
        try:
            usuario = app.state.usuarios.actualizar_contacto(usuario_id, body.correo, body.telefono)
        except DatoInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if usuario is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        logger_usuarios.info("contacto actualizado id=%s", id_opaco(usuario_id))
        return UsuarioResponse(**usuario)

    @app.get("/usuarios/{usuario_id}/contacto", tags=["usuarios"], response_model=ContactoResponse)
    def obtener_contacto(usuario_id: str) -> ContactoResponse:
        """El correo y el teléfono en claro de un CLIENTE. Es dato personal: AUTH es privado y
        solo lo llama POLICY, para enviarle al cliente el PDF de su póliza. 404 si el usuario no
        existe o no es CLIENTE. Nunca se loguea."""
        contacto = app.state.usuarios.contacto_de(usuario_id)
        if contacto is None:
            raise HTTPException(status_code=404, detail="usuario no encontrado")
        return ContactoResponse(**contacto)

    # ---------- Socios de distribución (HU-W07) ----------
    # Internos, como el resto: solo los llama el BFF, que ya validó el token del socio y pasa su id.

    @app.post("/socios/tokens", tags=["socios"], response_model=TokenSocioResponse)
    def emitir_token_socio(body: SolicitudTokenSocio) -> TokenSocioResponse:
        try:
            return TokenSocioResponse(**app.state.socios.emitir_token(body.client_id, body.clave))
        except CredencialesSocioInvalidas as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

    @app.get("/.well-known/jwks.json", tags=["socios"])
    def jwks_socios() -> dict:
        """Claves públicas de los tokens de socios: las lee el API Gateway y el BFF."""
        return app.state.socios.jwks()

    @app.post("/socios/nombres", tags=["socios"], response_model=dict[str, str])
    def nombres_de_socios(body: SolicitudNombres) -> dict[str, str]:
        """Nombre comercial de los socios pedidos, para la lista del BACKOFFICE (no es dato
        personal)."""
        try:
            return app.state.socios.nombres(body.ids)
        except DatoSocioInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/socios/{socio_id}", tags=["socios"], response_model=SocioResponse)
    def obtener_socio(socio_id: str) -> SocioResponse:
        socio = app.state.socios.obtener(socio_id)
        if socio is None:
            raise HTTPException(status_code=404, detail="socio no encontrado")
        return SocioResponse(**socio)

    @app.get(
        "/socios/{socio_id}/credenciales",
        tags=["socios"],
        response_model=list[CredencialSocioResponse],
    )
    def credenciales_socio(socio_id: str) -> list[CredencialSocioResponse]:
        credenciales = app.state.socios.credenciales(socio_id)
        if credenciales is None:
            raise HTTPException(status_code=404, detail="socio no encontrado")
        return [CredencialSocioResponse(**c) for c in credenciales]

    @app.post(
        "/socios/{socio_id}/credenciales/{entorno}/rotar",
        tags=["socios"],
        response_model=CredencialSocioResponse,
    )
    def rotar_credencial_socio(socio_id: str, entorno: Entorno) -> CredencialSocioResponse:
        credencial = app.state.socios.rotar(socio_id, entorno)
        if credencial is None:
            raise HTTPException(status_code=404, detail="socio no encontrado")
        return CredencialSocioResponse(**credencial)

    @app.post(
        "/socios/{socio_id}/presupuesto/{entorno}/{api}/reservar",
        tags=["socios"],
        response_model=PresupuestoResponse,
        responses={429: {"description": "presupuesto agotado"}},
    )
    def reservar_presupuesto(socio_id: str, entorno: Entorno, api: str) -> PresupuestoResponse:
        try:
            return PresupuestoResponse(restantes=app.state.socios.reservar(socio_id, entorno, api))
        except PresupuestoAgotado as error:
            raise HTTPException(status_code=429, detail="PRESUPUESTO_AGOTADO") from error
        except DatoSocioInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post(
        "/socios/{socio_id}/presupuesto/{entorno}/{api}/devolver",
        tags=["socios"],
        response_model=PresupuestoResponse,
    )
    def devolver_presupuesto(socio_id: str, entorno: Entorno, api: str) -> PresupuestoResponse:
        try:
            return PresupuestoResponse(restantes=app.state.socios.devolver(socio_id, entorno, api))
        except DatoSocioInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/socios/{socio_id}/consumos", tags=["socios"], status_code=204)
    def registrar_consumo_socio(socio_id: str, body: SolicitudConsumoSocio) -> Response:
        try:
            app.state.socios.registrar_consumo(
                socio_id, body.entorno, body.api, body.resultado, body.latencia_ms
            )
        except DatoSocioInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return Response(status_code=204)

    @app.get(
        "/socios/{socio_id}/consumo", tags=["socios"], response_model=ConsumoSocioResponse
    )
    def consumo_socio(
        socio_id: str, entorno: Entorno, desde: date | None = None, hasta: date | None = None
    ) -> ConsumoSocioResponse:
        try:
            return ConsumoSocioResponse(**app.state.socios.consumo(socio_id, entorno, desde, hasta))
        except DatoSocioInvalido as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
