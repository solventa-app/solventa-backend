"""Usuarios de Solventa (HU-M02): alta, consulta, cambio de rol y suspensión.

AUTH es el **único escritor** de la tabla `usuarios` (regla de arquitectura #1). El
esquema lo crea la migración `0002_usuarios`, nunca la app al arrancar (ADR-07).

Capa simple, sin puertos y adaptadores formales (regla #3): el `Protocol` existe solo
para probar con dobles sin levantar PostgreSQL.

Datos personales: el alias de acceso **no puede ser un correo ni un documento**
(`validar_alias`), así que la tabla no guarda datos personales que haya que cifrar
(SEG-002). La clave se guarda solo como hash argon2id y nunca sale de este servicio."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal, Protocol

from argon2 import PasswordHasher
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

Rol = Literal["CLIENTE", "ASESOR", "BACKOFFICE"]
ROLES: tuple[str, ...] = ("CLIENTE", "ASESOR", "BACKOFFICE")

ALIAS_VALIDO = re.compile(r"^[a-z0-9._-]{3,32}$")
# Seis o más dígitos seguidos: parece un número de documento.
PARECE_DOCUMENTO = re.compile(r"\d{6,}")
CLAVE_LONGITUD_MINIMA = 8
CLAVE_LONGITUD_MAXIMA = 128

# Columnas que pueden salir del servicio: nunca el hash ni el estado del bloqueo.
_PUBLICAS = "id, usuario, rol, estado, creado_en"
_TODAS = f"{_PUBLICAS}, hash_clave, intentos_fallidos, bloqueado_hasta"


class DatoInvalido(Exception):
    """La solicitud no cumple las reglas (alias, clave o rol). Mensaje apto para el cliente."""


class UsuarioDuplicado(Exception):
    """Ya existe un usuario con ese alias."""


def validar_alias(alias: str) -> None:
    # Primero la regla de datos personales: quien escribe un correo debe leer que no
    # puede usar un correo, no un mensaje genérico de formato.
    if "@" in alias or PARECE_DOCUMENTO.search(alias):
        raise DatoInvalido("El usuario no puede ser un correo ni un número de documento.")
    if not ALIAS_VALIDO.match(alias):
        raise DatoInvalido(
            "El usuario debe tener entre 3 y 32 caracteres: minúsculas, números, punto, "
            "guion o guion bajo."
        )


def validar_clave(clave: str) -> None:
    if not CLAVE_LONGITUD_MINIMA <= len(clave) <= CLAVE_LONGITUD_MAXIMA:
        raise DatoInvalido(
            f"La clave debe tener entre {CLAVE_LONGITUD_MINIMA} y {CLAVE_LONGITUD_MAXIMA} "
            "caracteres."
        )


def validar_rol(rol: str) -> None:
    if rol not in ROLES:
        raise DatoInvalido(f"Rol desconocido: {rol}.")


def publico(fila: dict[str, Any]) -> dict[str, Any]:
    """Lo único que se devuelve hacia afuera de un usuario (sin hash ni intentos)."""
    return {campo: fila[campo] for campo in ("id", "usuario", "rol", "estado", "creado_en")}


class RepositorioUsuarios(Protocol):
    def crear(self, usuario_id: str, alias: str, hash_clave: str, rol: str) -> dict | None: ...

    def obtener(self, usuario_id: str) -> dict | None: ...

    def obtener_por_alias(self, alias: str) -> dict | None: ...

    def listar(self, rol: str | None) -> list[dict]: ...

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None: ...

    def suspender(self, usuario_id: str) -> dict | None: ...

    def registrar_fallo(self, usuario_id: str, bloquear_hasta: datetime | None) -> int: ...

    def registrar_exito(self, usuario_id: str) -> None: ...


class RepositorioUsuariosPostgres:
    """Cada sentencia es una transacción (`autocommit`), igual que en PAYMENTS."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _una(self, sql: str, parametros: dict) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(sql, parametros).fetchone()
            return dict(fila) if fila is not None else None

    def crear(self, usuario_id: str, alias: str, hash_clave: str, rol: str) -> dict | None:
        """`None` si el alias ya existe (índice único: correcto también bajo concurrencia)."""
        return self._una(
            f"""
            INSERT INTO usuarios (id, usuario, hash_clave, rol)
            VALUES (%(id)s, %(usuario)s, %(hash_clave)s, %(rol)s)
            ON CONFLICT (usuario) DO NOTHING
            RETURNING {_TODAS}
            """,
            {"id": usuario_id, "usuario": alias, "hash_clave": hash_clave, "rol": rol},
        )

    def obtener(self, usuario_id: str) -> dict | None:
        return self._una(f"SELECT {_TODAS} FROM usuarios WHERE id = %(id)s", {"id": usuario_id})

    def obtener_por_alias(self, alias: str) -> dict | None:
        return self._una(
            f"SELECT {_TODAS} FROM usuarios WHERE usuario = %(usuario)s", {"usuario": alias}
        )

    def listar(self, rol: str | None) -> list[dict]:
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"""
                SELECT {_TODAS} FROM usuarios
                WHERE %(rol)s::text IS NULL OR rol = %(rol)s
                ORDER BY creado_en
                """,
                {"rol": rol},
            ).fetchall()
            return [dict(fila) for fila in filas]

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None:
        return self._una(
            f"""
            UPDATE usuarios SET rol = %(rol)s, actualizado_en = now()
            WHERE id = %(id)s RETURNING {_TODAS}
            """,
            {"id": usuario_id, "rol": rol},
        )

    def suspender(self, usuario_id: str) -> dict | None:
        return self._una(
            f"""
            UPDATE usuarios SET estado = 'SUSPENDIDO', actualizado_en = now()
            WHERE id = %(id)s RETURNING {_TODAS}
            """,
            {"id": usuario_id},
        )

    def registrar_fallo(self, usuario_id: str, bloquear_hasta: datetime | None) -> int:
        """Suma un intento fallido y, si `bloquear_hasta` llega, bloquea y reinicia el contador."""
        fila = self._una(
            """
            UPDATE usuarios SET
                intentos_fallidos = CASE WHEN %(bloquear)s::timestamptz IS NULL
                                         THEN intentos_fallidos + 1 ELSE 0 END,
                bloqueado_hasta = COALESCE(%(bloquear)s::timestamptz, bloqueado_hasta),
                actualizado_en = now()
            WHERE id = %(id)s RETURNING intentos_fallidos
            """,
            {"id": usuario_id, "bloquear": bloquear_hasta},
        )
        return fila["intentos_fallidos"] if fila else 0

    def registrar_exito(self, usuario_id: str) -> None:
        self._una(
            """
            UPDATE usuarios SET intentos_fallidos = 0, bloqueado_hasta = NULL
            WHERE id = %(id)s RETURNING id
            """,
            {"id": usuario_id},
        )


def construir_pool(database_url: str) -> ConnectionPool:
    """Copiado de `services/payments/app/application/repositorio_cobros.py`
    (sin código compartido entre servicios)."""
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )


class ServicioUsuarios:
    """Alta y administración de usuarios. AUTH no decide quién puede llamar a esto:
    el BFF exige rol BACKOFFICE antes de llegar aquí, y AUTH es privado en Cloud Run."""

    def __init__(self, repositorio: RepositorioUsuarios, hasher: PasswordHasher) -> None:
        self._repositorio = repositorio
        self._hasher = hasher

    def crear(self, alias: str, clave: str, rol: str) -> dict:
        validar_alias(alias)
        validar_clave(clave)
        validar_rol(rol)
        creado = self._repositorio.crear(str(uuid.uuid4()), alias, self._hasher.hash(clave), rol)
        if creado is None:
            raise UsuarioDuplicado("Ya existe un usuario con ese alias.")
        return publico(creado)

    def obtener(self, usuario_id: str) -> dict | None:
        fila = self._repositorio.obtener(usuario_id)
        return publico(fila) if fila else None

    def listar(self, rol: str | None) -> list[dict]:
        if rol is not None:
            validar_rol(rol)
        return [publico(fila) for fila in self._repositorio.listar(rol)]

    def cambiar_rol(self, usuario_id: str, rol: str) -> dict | None:
        validar_rol(rol)
        fila = self._repositorio.cambiar_rol(usuario_id, rol)
        return publico(fila) if fila else None

    def suspender(self, usuario_id: str) -> dict | None:
        """Baja lógica: el usuario se conserva, pero ya no puede iniciar sesión."""
        fila = self._repositorio.suspender(usuario_id)
        return publico(fila) if fila else None
