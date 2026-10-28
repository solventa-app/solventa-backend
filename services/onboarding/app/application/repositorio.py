"""Sesiones de onboarding en PostgreSQL (HU-M01).

Capa simple (regla 3). ONBOARDING es el **único escritor** de su base (regla 1). Los cambios de
estado son condicionales (`UPDATE ... WHERE estado = ...`): si dos tareas de verificación corren a
la vez, solo una cierra la sesión. El pool va en `autocommit` (una sentencia = una transacción)."""

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_COLUMNAS = (
    "id, estado, intento, token_hash, token_vence_en, jurisdiccion, tipo_documento, "
    "consentimiento_id, usuario_id, titular_id, anverso_id, reverso_id, rostro_id, video_id, "
    "motivo, cuenta_notificada_en, creado_en, actualizado_en"
)
_ACTUALIZABLES = frozenset(
    {
        "estado",
        "intento",
        "consentimiento_id",
        "usuario_id",
        "titular_id",
        "anverso_id",
        "reverso_id",
        "rostro_id",
        "video_id",
        "motivo",
        "cuenta_notificada_en",
    }
)


class TitularYaVerificado(Exception):
    """Otra sesión VERIFICADA ya tiene ese `titular_id` (índice único parcial)."""


class RepositorioPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def crear(self, sesion: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                INSERT INTO sesiones_onboarding
                    (id, token_hash, token_vence_en, jurisdiccion, tipo_documento)
                VALUES (%(id)s, %(token_hash)s, %(token_vence_en)s, %(jurisdiccion)s,
                        %(tipo_documento)s)
                RETURNING {_COLUMNAS}
                """,
                sesion,
            ).fetchone()

    def obtener(self, sesion_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"SELECT {_COLUMNAS} FROM sesiones_onboarding WHERE id = %(id)s", {"id": sesion_id}
            ).fetchone()

    def obtener_por_usuario(self, usuario_id: str) -> dict[str, Any] | None:
        """La sesión más reciente de la cuenta (el BFF la pide para `miVerificacion`)."""
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM sesiones_onboarding WHERE usuario_id = %(u)s
                ORDER BY creado_en DESC LIMIT 1
                """,
                {"u": usuario_id},
            ).fetchone()

    def actualizar(
        self, sesion_id: str, cambios: dict[str, Any], estados: tuple[str, ...]
    ) -> dict[str, Any] | None:
        """Aplica `cambios` solo si la sesión está en uno de `estados`; `None` si no."""
        desconocidas = set(cambios) - _ACTUALIZABLES
        if desconocidas:
            raise ValueError(f"columnas no actualizables: {sorted(desconocidas)}")
        asignaciones = ", ".join(f"{c} = %({c})s" for c in cambios)
        try:
            with self._pool.connection() as conexion:
                return conexion.execute(
                    f"""
                    UPDATE sesiones_onboarding SET {asignaciones}, actualizado_en = now()
                    WHERE id = %(_id)s AND estado = ANY(%(_estados)s)
                    RETURNING {_COLUMNAS}
                    """,
                    {**cambios, "_id": sesion_id, "_estados": list(estados)},
                ).fetchone()
        except psycopg.errors.UniqueViolation as error:
            raise TitularYaVerificado() from error

    def titular_verificado_en_otra(self, titular_id: str, sesion_id: str) -> bool:
        with self._pool.connection() as conexion:
            return (
                conexion.execute(
                    """
                    SELECT 1 FROM sesiones_onboarding
                    WHERE titular_id = %(t)s AND estado = 'VERIFICADA' AND id <> %(id)s
                    """,
                    {"t": titular_id, "id": sesion_id},
                ).fetchone()
                is not None
            )

    def guardar_datos(self, sesion_id: str, datos: dict[str, str | None]) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                """
                INSERT INTO datos_extraidos
                    (sesion_id, nombres, apellidos, numero, fecha_nacimiento, pais)
                VALUES (%(sesion_id)s, %(nombres)s, %(apellidos)s, %(numero)s,
                        %(fecha_nacimiento)s, %(pais)s)
                ON CONFLICT (sesion_id) DO UPDATE SET
                    nombres = EXCLUDED.nombres, apellidos = EXCLUDED.apellidos,
                    numero = EXCLUDED.numero, fecha_nacimiento = EXCLUDED.fecha_nacimiento,
                    pais = EXCLUDED.pais
                """,
                {"sesion_id": sesion_id, **datos},
            )

    def obtener_datos(self, sesion_id: str) -> dict[str, Any] | None:
        """Los datos cifrados que leyó el proveedor (solo existen en sesiones aprobadas)."""
        with self._pool.connection() as conexion:
            return conexion.execute(
                "SELECT nombres, apellidos, numero FROM datos_extraidos WHERE sesion_id = %(s)s",
                {"s": sesion_id},
            ).fetchone()

    def registrar_verificacion(self, fila: dict[str, Any]) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                """
                INSERT INTO verificaciones
                    (sesion_id, intento, estado, motivo, puntaje_rostro, puntaje_vida, alertas)
                VALUES (%(sesion_id)s, %(intento)s, %(estado)s, %(motivo)s, %(puntaje_rostro)s,
                        %(puntaje_vida)s, %(alertas)s)
                """,
                fila,
            )

    def registrar_evento(self, sesion_id: str, tipo: str, detalle: str | None = None) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "INSERT INTO eventos_onboarding (sesion_id, tipo, detalle) "
                "VALUES (%(s)s, %(t)s, %(d)s)",
                {"s": sesion_id, "t": tipo, "d": detalle},
            )


def construir_pool(database_url: str) -> ConnectionPool:
    if not database_url:
        raise ValueError("Falta DATABASE_URL")
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
