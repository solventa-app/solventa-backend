"""Dispositivos de push (KAN-259, HU-M04): un solo dispositivo con push activo por cliente.

No tiene nada que ver con `app/dispositivos.py` (login de confianza con huella o rostro, KAN-26):
esa guarda la llave pública de un teléfono para entrar sin clave; esta guarda el token de FCM del
teléfono para mandarle notificaciones. Son registros independientes del mismo teléfono.

Al iniciar sesión en la app en un dispositivo nuevo, `ServicioSesiones` revoca las demás cadenas de
refresh `app` del cliente (`app/refrescos.py`, D3: una sola sesión activa por cliente, solo entre
sesiones de la app) y llama aquí para dejar un solo dispositivo con push activo (índice único
parcial en la base: `dispositivos_push_un_activo_por_usuario`). El token FCM se registra o rota
aparte, cuando la app lo obtiene o cuando Firebase lo renueva (`onTokenRefresh`).

Regla #6: el token vive cifrado con el mismo `CifradorCampos` que el correo y el teléfono, con el
`usuario_id` como contexto. Solo se descifra en `destino_push`, que únicamente llama la Cloud Run
function `enviar-push` (nunca un humano ni otro servicio)."""

import hashlib
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from psycopg_pool import ConnectionPool

from app.cifrado import CifradorCampos

logger = logging.getLogger("auth.dispositivos_push")

PLATAFORMAS = ("android", "ios")

MOTIVO_NUEVA_SESION = "NUEVA_SESION"
MOTIVO_CIERRE_SESION = "CIERRE_SESION"
MOTIVO_TOKEN_INVALIDO = "TOKEN_INVALIDO"
MOTIVO_CUENTA_SUSPENDIDA = "CUENTA_SUSPENDIDA"


class DatoInvalido(Exception):
    """`instalacionId`, `plataforma` o el token están vacíos o son inválidos."""


class SinDispositivoActivo(Exception):
    """No hay un dispositivo con push activo para este destinatario."""


class PushDeshabilitado(SinDispositivoActivo):
    """El cliente apagó las notificaciones (`usuarios.notificaciones_push = false`)."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validar_instalacion(instalacion_id: str, plataforma: str) -> None:
    if not instalacion_id or not instalacion_id.strip():
        raise DatoInvalido("instalacionId es obligatorio")
    if plataforma not in PLATAFORMAS:
        raise DatoInvalido(f"plataforma debe ser una de: {', '.join(PLATAFORMAS)}")


class RepositorioDispositivosPush(Protocol):
    def desactivar_activos(self, usuario_id: str, motivo: str, ahora: datetime) -> None: ...

    def desactivar_por_familia(
        self, usuario_id: str, familia_id: str, motivo: str, ahora: datetime
    ) -> None: ...

    def crear_o_reactivar(
        self,
        dispositivo_id: str,
        usuario_id: str,
        instalacion_id: str,
        plataforma: str,
        familia_id: str,
        ahora: datetime,
    ) -> dict: ...

    def actualizar_token(
        self,
        usuario_id: str,
        instalacion_id: str,
        token_cifrado: str,
        token_hash: str,
        ahora: datetime,
    ) -> dict | None:
        """`None` si el usuario no tiene un dispositivo activo con esa `instalacionId`."""
        ...

    def desactivar_otro_usuario_con_token(
        self, usuario_id_actual: str, token_hash: str, motivo: str, ahora: datetime
    ) -> None:
        """Teléfono compartido: si OTRO usuario tiene activo este mismo token, lo apaga."""
        ...

    def activo_de(self, usuario_id: str) -> dict | None: ...

    def marcar_token_invalido(self, dispositivo_id: str, ahora: datetime) -> None: ...


class ServicioDispositivosPush:
    def __init__(
        self,
        repositorio: RepositorioDispositivosPush,
        cifrador: CifradorCampos,
        habilitado_push: Callable[[str], bool],
    ) -> None:
        self._repositorio = repositorio
        self._cifrador = cifrador
        # `usuarios.notificaciones_push`: se lee con un callback para no acoplar este módulo a
        # `RepositorioUsuarios` entero (solo necesita esa única columna).
        self._habilitado_push = habilitado_push

    def registrar_sesion(
        self,
        usuario_id: str,
        instalacion_id: str,
        plataforma: str,
        familia_id: str,
        ahora: datetime,
    ) -> None:
        """Sesión nueva en la app (ya se revocaron las demás cadenas `app`, D3): deja este
        dispositivo como el único con push activo del cliente."""
        _validar_instalacion(instalacion_id, plataforma)
        self._repositorio.desactivar_activos(usuario_id, MOTIVO_NUEVA_SESION, ahora)
        self._repositorio.crear_o_reactivar(
            str(uuid.uuid4()), usuario_id, instalacion_id, plataforma, familia_id, ahora
        )
        logger.info("dispositivo de push activo usuario=%s", usuario_id)

    def registrar_token(
        self, usuario_id: str, instalacion_id: str, token: str, ahora: datetime
    ) -> None:
        """Registra el token al obtenerlo por primera vez, o lo rota (`onTokenRefresh`)."""
        if not token or not token.strip():
            raise DatoInvalido("token es obligatorio")
        token = token.strip()
        token_hash = hash_token(token)
        token_cifrado = self._cifrador.cifrar(token, usuario_id)
        dispositivo = self._repositorio.actualizar_token(
            usuario_id, instalacion_id, token_cifrado, token_hash, ahora
        )
        if dispositivo is None:
            raise DatoInvalido(
                "no hay un dispositivo con push activo de esta instalación para este usuario"
            )
        self._repositorio.desactivar_otro_usuario_con_token(
            usuario_id, token_hash, MOTIVO_NUEVA_SESION, ahora
        )

    def cerrar_sesion(self, usuario_id: str, familia_id: str, ahora: datetime) -> None:
        """T22 `cerrarSesion`: apaga el dispositivo de push de esta cadena, si la tenía."""
        self._repositorio.desactivar_por_familia(
            usuario_id, familia_id, MOTIVO_CIERRE_SESION, ahora
        )

    def revocar_de_usuario(self, usuario_id: str, ahora: datetime) -> None:
        """Suspender la cuenta o cambiar la clave: ningún dispositivo del usuario sigue con push
        activo."""
        self._repositorio.desactivar_activos(usuario_id, MOTIVO_CUENTA_SUSPENDIDA, ahora)

    def destino_push(self, usuario_id: str) -> dict:
        """`{dispositivo_id, token, plataforma}` del único dispositivo activo con token, si el
        cliente tiene el push encendido. Lo llama solo la función `enviar-push`."""
        if not self._habilitado_push(usuario_id):
            raise PushDeshabilitado()
        dispositivo = self._repositorio.activo_de(usuario_id)
        if dispositivo is None or not dispositivo.get("token_fcm_cifrado"):
            raise SinDispositivoActivo()
        token = self._cifrador.descifrar(dispositivo["token_fcm_cifrado"], usuario_id)
        return {
            "dispositivo_id": dispositivo["id"],
            "token": token,
            "plataforma": dispositivo["plataforma"],
        }

    def token_invalido(self, dispositivo_id: str, ahora: datetime) -> None:
        """FCM respondió `UNREGISTERED`/`INVALID_ARGUMENT`: se borra el token, la sesión sigue
        viva (la app lo vuelve a registrar al abrirse)."""
        self._repositorio.marcar_token_invalido(dispositivo_id, ahora)


_TODAS = (
    "id, usuario_id, instalacion_id, plataforma, familia_id, token_fcm_cifrado, "
    "token_fcm_hash, activo, version_app, modelo, creado_en, token_actualizado_en, "
    "desactivado_en, motivo_desactivacion"
)


class RepositorioDispositivosPushPostgres:
    """Cada sentencia es una transacción (`autocommit`), igual que `RepositorioUsuariosPostgres`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _una(self, sql: str, parametros: dict) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(sql, parametros).fetchone()
            return dict(fila) if fila is not None else None

    def desactivar_activos(self, usuario_id: str, motivo: str, ahora: datetime) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE dispositivos_push SET activo = false, desactivado_en = %(a)s, "
                "motivo_desactivacion = %(m)s WHERE usuario_id = %(u)s AND activo",
                {"u": usuario_id, "m": motivo, "a": ahora},
            )

    def desactivar_por_familia(
        self, usuario_id: str, familia_id: str, motivo: str, ahora: datetime
    ) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE dispositivos_push SET activo = false, desactivado_en = %(a)s, "
                "motivo_desactivacion = %(m)s "
                "WHERE usuario_id = %(u)s AND familia_id = %(f)s AND activo",
                {"u": usuario_id, "f": familia_id, "m": motivo, "a": ahora},
            )

    def crear_o_reactivar(
        self,
        dispositivo_id: str,
        usuario_id: str,
        instalacion_id: str,
        plataforma: str,
        familia_id: str,
        ahora: datetime,
    ) -> dict:
        fila = self._una(
            f"""
            INSERT INTO dispositivos_push
                (id, usuario_id, instalacion_id, plataforma, familia_id, activo, creado_en)
            VALUES (%(id)s, %(u)s, %(inst)s, %(plat)s, %(fam)s, true, %(a)s)
            ON CONFLICT (usuario_id, instalacion_id) DO UPDATE SET
                plataforma = %(plat)s, familia_id = %(fam)s, activo = true,
                desactivado_en = NULL, motivo_desactivacion = NULL
            RETURNING {_TODAS}
            """,
            {
                "id": dispositivo_id,
                "u": usuario_id,
                "inst": instalacion_id,
                "plat": plataforma,
                "fam": familia_id,
                "a": ahora,
            },
        )
        assert fila is not None
        return fila

    def actualizar_token(
        self,
        usuario_id: str,
        instalacion_id: str,
        token_cifrado: str,
        token_hash: str,
        ahora: datetime,
    ) -> dict | None:
        return self._una(
            f"""
            UPDATE dispositivos_push SET token_fcm_cifrado = %(t)s, token_fcm_hash = %(h)s,
                token_actualizado_en = %(a)s
            WHERE usuario_id = %(u)s AND instalacion_id = %(inst)s AND activo
            RETURNING {_TODAS}
            """,
            {
                "u": usuario_id,
                "inst": instalacion_id,
                "t": token_cifrado,
                "h": token_hash,
                "a": ahora,
            },
        )

    def desactivar_otro_usuario_con_token(
        self, usuario_id_actual: str, token_hash: str, motivo: str, ahora: datetime
    ) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE dispositivos_push SET activo = false, desactivado_en = %(a)s, "
                "motivo_desactivacion = %(m)s, token_fcm_cifrado = NULL, token_fcm_hash = NULL "
                "WHERE token_fcm_hash = %(h)s AND usuario_id != %(u)s AND activo",
                {"u": usuario_id_actual, "h": token_hash, "m": motivo, "a": ahora},
            )

    def activo_de(self, usuario_id: str) -> dict | None:
        return self._una(
            f"SELECT {_TODAS} FROM dispositivos_push WHERE usuario_id = %(u)s AND activo",
            {"u": usuario_id},
        )

    def marcar_token_invalido(self, dispositivo_id: str, ahora: datetime) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE dispositivos_push SET token_fcm_cifrado = NULL, token_fcm_hash = NULL, "
                "token_actualizado_en = %(a)s, motivo_desactivacion = %(m)s "
                "WHERE id = %(id)s",
                {"id": dispositivo_id, "m": MOTIVO_TOKEN_INVALIDO, "a": ahora},
            )


__all__ = [
    "DatoInvalido",
    "PushDeshabilitado",
    "RepositorioDispositivosPush",
    "RepositorioDispositivosPushPostgres",
    "ServicioDispositivosPush",
    "SinDispositivoActivo",
    "hash_token",
]
