"""Inicio de sesión (HU-M02): valida alias + clave y emite un JWT HS256.

El JWT lo verifica el BFF con el mismo secreto (`JWT_SECRETO`, secreto `clave-jwt`).
Cuando llegue el API Gateway se pasará a ES256 + JWKS (ver
`docs/autenticacion-usuarios.md`).

Reglas de seguridad:
- La clave se compara con `PasswordHasher.verify` de argon2 (tiempo constante).
- Si el alias no existe se verifica igual contra un hash ficticio: el tiempo de
  respuesta no revela qué alias existen.
- Siempre el mismo mensaje para alias inexistente, clave errónea, usuario
  bloqueado o suspendido.
- Tras `MAX_INTENTOS` fallos seguidos el usuario queda bloqueado `MINUTOS_BLOQUEO`.
- Nunca se loguea el alias, la clave ni el token: solo `id_opaco` del usuario.

Cuentas del onboarding del móvil (HU-M01): entran con su **correo** (se busca por su HMAC) y
mientras la identidad no esté verificada (`PENDIENTE_VERIFICACION` o `RECHAZADA`):
- en el canal `app` reciben un token con el único scope `verificacion-pendiente` (home limitado);
- en cualquier otro canal, `VerificacionPendiente` (el BFF responde `VERIFICACION_PENDIENTE`).
Ese rechazo solo se da con la clave correcta y no cuenta como intento fallido."""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.identificadores import id_opaco
from app.refrescos import RepositorioRefrescos, hash_refresh, nuevo_refresh
from app.usuarios import (
    PENDIENTE_VERIFICACION,
    RECHAZADA,
    DatoInvalido,
    RepositorioUsuarios,
    correo_hmac,
    normalizar_correo,
    publico,
)

logger = logging.getLogger("auth.sesiones")

ALGORITMO_JWT = "HS256"
MAX_INTENTOS = 5
MINUTOS_BLOQUEO = 15
MENSAJE_CREDENCIALES = "Usuario o clave incorrectos."

# Scopes que viajan en el token (HU-M02 "JWT con scopes"). Hoy el BFF autoriza por rol;
# los scopes quedan listos para un control más fino o para el API Gateway.
SCOPES_POR_ROL: dict[str, list[str]] = {
    "CLIENTE": ["consentimiento", "oferta"],
    "ASESOR": ["consentimiento", "oferta"],
    "BACKOFFICE": ["cobros", "usuarios"],
}
SCOPES_VERIFICACION_PENDIENTE = ["verificacion-pendiente"]
ESTADOS_SIN_VERIFICAR = (PENDIENTE_VERIFICACION, RECHAZADA)
CANAL_APP = "app"
CANAL_WEB = "web"
MENSAJE_REFRESH = "Tu sesión ya no es válida. Inicia sesión de nuevo."


class CredencialesInvalidas(Exception):
    """Alias o clave incorrectos, o usuario bloqueado o suspendido (mismo mensaje siempre)."""


class VerificacionPendiente(Exception):
    """Clave correcta, pero la identidad aún no está verificada y el canal no es `app`."""


class RefreshInvalido(Exception):
    """Refresh inexistente, vencido, usado, revocado o de otro canal: mismo mensaje siempre, sin
    revelar el motivo (T22)."""


def canal_de_sesion(canal: str | None) -> str:
    """Todo canal que no sea la app es web: un refresh de uno no sirve en el otro."""
    return CANAL_APP if canal == CANAL_APP else CANAL_WEB


class ServicioSesiones:
    def __init__(
        self,
        repositorio: RepositorioUsuarios,
        hasher: PasswordHasher,
        jwt_secreto: str,
        vigencia_minutos: int,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        clave_correo: str = "",
        refrescos: RepositorioRefrescos | None = None,
        refresh_dias: int = 8,
    ) -> None:
        self._refrescos = refrescos
        self._refresh_vigencia = timedelta(days=refresh_dias)
        self._clave_correo = clave_correo
        self._repositorio = repositorio
        self._hasher = hasher
        self._secreto = jwt_secreto
        self._vigencia = timedelta(minutes=vigencia_minutos)
        self._reloj = reloj
        # Hash de una clave que nadie conoce: se verifica cuando el alias no existe.
        self._hash_ficticio = hasher.hash(uuid.uuid4().hex)

    def _buscar(self, alias: str) -> dict | None:
        if "@" not in alias:
            return self._repositorio.obtener_por_alias(alias)
        if not self._clave_correo:
            return None
        try:
            limpio = normalizar_correo(alias)
        except DatoInvalido:
            return None
        return self._repositorio.obtener_por_correo_hmac(correo_hmac(limpio, self._clave_correo))

    def iniciar_sesion(
        self, alias: str, clave: str, canal: str | None = None, rol: str | None = None
    ) -> dict:
        """Devuelve `{token, expira_en, usuario}` o lanza `CredencialesInvalidas` o
        `VerificacionPendiente`. `alias` puede ser el alias o el correo (cuentas de M01)."""
        fila = self._buscar(alias)
        if fila is None:
            self._verificar(self._hash_ficticio, clave)
            raise CredencialesInvalidas(MENSAJE_CREDENCIALES)

        ahora = self._reloj()
        clave_correcta = self._verificar(fila["hash_clave"], clave)
        bloqueado = fila["bloqueado_hasta"] is not None and ahora < fila["bloqueado_hasta"]
        # T17: un rol pedido que no es el de la cuenta, o un rol que no entra por la app, se
        # trata exactamente como una clave errada (mismo mensaje, mismo trabajo, mismo conteo).
        rol_no_permitido = (rol is not None and rol != fila["rol"]) or (
            canal == CANAL_APP and fila["rol"] != "CLIENTE"
        )
        clave_correcta = clave_correcta and not rol_no_permitido

        if bloqueado:
            logger.info("inicio de sesión rechazado: bloqueado usuario=%s", id_opaco(fila["id"]))
            raise CredencialesInvalidas(MENSAJE_CREDENCIALES)
        if not clave_correcta:
            self._registrar_fallo(fila, ahora)
            raise CredencialesInvalidas(MENSAJE_CREDENCIALES)
        if fila["estado"] in ESTADOS_SIN_VERIFICAR and canal != CANAL_APP:
            self._repositorio.registrar_exito(fila["id"])
            logger.info(
                "inicio de sesión fuera de la app sin verificar usuario=%s", id_opaco(fila["id"])
            )
            raise VerificacionPendiente()
        if fila["estado"] not in ("ACTIVO", *ESTADOS_SIN_VERIFICAR):
            logger.info("inicio de sesión rechazado: suspendido usuario=%s", id_opaco(fila["id"]))
            raise CredencialesInvalidas(MENSAJE_CREDENCIALES)

        self._repositorio.registrar_exito(fila["id"])
        sesion = self._emitir(fila, ahora, ahora + self._vigencia)
        self._agregar_refresh(sesion, fila["id"], str(uuid.uuid4()), canal_de_sesion(canal), ahora)
        logger.info("sesión iniciada usuario=%s rol=%s", id_opaco(fila["id"]), fila["rol"])
        return sesion

    def renovar_permisos(self, usuario_id: str, expira_en: datetime) -> dict:
        """`actualizarSesion` (M01): relee el estado y emite un token con los scopes de ahora y
        **la misma expiración**: no alarga la sesión. El BFF ya validó el token vigente."""
        fila = self._repositorio.obtener(usuario_id)
        ahora = self._reloj()
        if (
            fila is None
            or expira_en <= ahora
            or fila["estado"]
            not in (
                "ACTIVO",
                *ESTADOS_SIN_VERIFICAR,
            )
        ):
            raise CredencialesInvalidas(MENSAJE_CREDENCIALES)
        return self._emitir(fila, ahora, expira_en)

    def _agregar_refresh(
        self, sesion: dict, usuario_id: str, familia_id: str, canal: str, ahora: datetime
    ) -> None:
        """Guarda el hash de un refresh nuevo (cadena `familia_id`) y lo agrega a la sesión."""
        if self._refrescos is None:
            return
        refresh = nuevo_refresh()
        expira = ahora + self._refresh_vigencia
        self._refrescos.crear(
            str(uuid.uuid4()), familia_id, usuario_id, hash_refresh(refresh), canal, expira
        )
        sesion["refresh_token"] = refresh
        sesion["refresh_expira_en"] = expira

    def renovar_con_refresh(self, refresh: str, canal: str | None) -> dict:
        """Cambia un refresh vigente por un par nuevo (acceso + refresh) y deja el anterior usado.

        Revalida estado y rol en la base. Reusar un refresh ya usado revoca toda la cadena.
        Lanza `RefreshInvalido` (mismo mensaje) en cualquier otro caso."""
        if self._refrescos is None:
            raise RefreshInvalido(MENSAJE_REFRESH)
        ahora = self._reloj()
        fila = self._refrescos.obtener_por_hash(hash_refresh(refresh))
        if fila is None or fila["canal"] != canal_de_sesion(canal):
            raise RefreshInvalido(MENSAJE_REFRESH)
        if fila["revocado_en"] is not None or fila["expira_en"] <= ahora:
            raise RefreshInvalido(MENSAJE_REFRESH)
        familia = fila["familia_id"]
        if fila["usado_en"] is not None or not self._refrescos.marcar_usado(fila["id"], ahora):
            self._refrescos.revocar_familia(familia, ahora)
            logger.warning(
                "refresh reusado: cadena revocada usuario=%s canal=%s",
                id_opaco(fila["usuario_id"]),
                fila["canal"],
            )
            raise RefreshInvalido(MENSAJE_REFRESH)
        usuario = self._repositorio.obtener(fila["usuario_id"])
        # En la web una cuenta sin verificar no renueva; en la app sí (alcance de onboarding).
        permitido = usuario is not None and (
            usuario["estado"] == "ACTIVO"
            or (usuario["estado"] in ESTADOS_SIN_VERIFICAR and fila["canal"] == CANAL_APP)
        )
        if not permitido:
            self._refrescos.revocar_familia(familia, ahora)
            raise RefreshInvalido(MENSAJE_REFRESH)
        sesion = self._emitir(usuario, ahora, ahora + self._vigencia)
        self._agregar_refresh(sesion, usuario["id"], familia, fila["canal"], ahora)
        logger.info("sesión renovada con refresh usuario=%s", id_opaco(usuario["id"]))
        return sesion

    def cerrar_sesion(self, refresh: str) -> None:
        """Revoca la cadena del refresh dado. Idempotente y sin revelar si existía."""
        if self._refrescos is None:
            return
        fila = self._refrescos.obtener_por_hash(hash_refresh(refresh))
        if fila is not None:
            self._refrescos.revocar_familia(fila["familia_id"], self._reloj())

    def revocar_de_usuario(self, usuario_id: str) -> None:
        """Al suspender la cuenta o cambiar la clave: ningún refresh del usuario vuelve a servir."""
        if self._refrescos is not None:
            self._refrescos.revocar_de_usuario(usuario_id, self._reloj())

    def _emitir(self, fila: dict, ahora: datetime, expira_en: datetime) -> dict:
        scopes = (
            SCOPES_VERIFICACION_PENDIENTE
            if fila["estado"] in ESTADOS_SIN_VERIFICAR
            else SCOPES_POR_ROL[fila["rol"]]
        )
        token = jwt.encode(
            {
                "sub": fila["id"],
                "usuario": fila["usuario"],
                "rol": fila["rol"],
                "scopes": scopes,
                "iat": int(ahora.timestamp()),
                "exp": int(expira_en.timestamp()),
                "jti": str(uuid.uuid4()),
            },
            self._secreto,
            algorithm=ALGORITMO_JWT,
        )
        return {"token": token, "expira_en": expira_en, "usuario": publico(fila)}

    def _verificar(self, hash_clave: str, clave: str) -> bool:
        try:
            return self._hasher.verify(hash_clave, clave)
        except VerificationError:
            return False

    def _registrar_fallo(self, fila: dict, ahora: datetime) -> None:
        bloquear = fila["intentos_fallidos"] + 1 >= MAX_INTENTOS
        bloquear_hasta = ahora + timedelta(minutes=MINUTOS_BLOQUEO) if bloquear else None
        self._repositorio.registrar_fallo(fila["id"], bloquear_hasta)
        if bloquear:
            logger.warning(
                "usuario bloqueado %d min tras %d intentos usuario=%s",
                MINUTOS_BLOQUEO,
                MAX_INTENTOS,
                id_opaco(fila["id"]),
            )
