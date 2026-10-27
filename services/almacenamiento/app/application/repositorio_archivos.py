"""Metadatos de los archivos en PostgreSQL (KAN-250).

Capa simple (regla 3). ALMACENAMIENTO es el **único escritor** de `archivos` y `accesos_archivo`
(regla 1). Los cambios de estado son condicionales (`UPDATE ... WHERE estado = 'PENDIENTE'`): si la
notificación de Cloud Storage y la confirmación del cliente llegan a la vez, solo una gana, y solo
esa publica el evento. El pool va en `autocommit` (una sentencia = una transacción)."""

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_COLUMNAS = (
    "id, tipo, estado, propietario_tipo, propietario_id, subido_por, subido_por_id, "
    "consentimiento_id, tipo_contenido, tamano_declarado, tamano_bytes, crc32c, objeto, "
    "derivado_de, motivo, retener_desde, creado_en, disponible_en, eliminado_en, actualizado_en"
)


class RepositorioArchivosPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def crear(self, archivo: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                INSERT INTO archivos
                    (id, tipo, propietario_tipo, propietario_id, subido_por, subido_por_id,
                     consentimiento_id, tipo_contenido, tamano_declarado, objeto, derivado_de)
                VALUES
                    (%(id)s, %(tipo)s, %(propietario_tipo)s, %(propietario_id)s, %(subido_por)s,
                     %(subido_por_id)s, %(consentimiento_id)s, %(tipo_contenido)s,
                     %(tamano_declarado)s, %(objeto)s, %(derivado_de)s)
                RETURNING {_COLUMNAS}
                """,
                archivo,
            ).fetchone()

    def obtener(self, archivo_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"SELECT {_COLUMNAS} FROM archivos WHERE id = %(id)s", {"id": archivo_id}
            ).fetchone()

    def obtener_por_objeto(self, objeto: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"SELECT {_COLUMNAS} FROM archivos WHERE objeto = %(objeto)s", {"objeto": objeto}
            ).fetchone()

    def listar_por_propietario(
        self, propietario_tipo: str, propietario_id: str, limite: int = 200
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM archivos
                WHERE propietario_tipo = %(tipo)s AND propietario_id = %(id)s
                ORDER BY creado_en DESC LIMIT %(limite)s
                """,
                {"tipo": propietario_tipo, "id": propietario_id, "limite": limite},
            ).fetchall()

    def marcar_disponible(
        self, archivo_id: str, tamano_bytes: int, crc32c: str | None
    ) -> dict[str, Any] | None:
        """`None` si otro ya lo resolvió (no estaba PENDIENTE)."""
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                UPDATE archivos
                SET estado = 'DISPONIBLE', tamano_bytes = %(tamano)s, crc32c = %(crc32c)s,
                    disponible_en = now(), actualizado_en = now()
                WHERE id = %(id)s AND estado = 'PENDIENTE'
                RETURNING {_COLUMNAS}
                """,
                {"id": archivo_id, "tamano": tamano_bytes, "crc32c": crc32c},
            ).fetchone()

    def marcar_rechazado(self, archivo_id: str, motivo: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                UPDATE archivos
                SET estado = 'RECHAZADO', motivo = %(motivo)s, actualizado_en = now()
                WHERE id = %(id)s AND estado = 'PENDIENTE'
                RETURNING {_COLUMNAS}
                """,
                {"id": archivo_id, "motivo": motivo},
            ).fetchone()

    def marcar_eliminado(self, archivo_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                UPDATE archivos
                SET estado = 'ELIMINADO', eliminado_en = now(), actualizado_en = now()
                WHERE id = %(id)s AND estado = 'DISPONIBLE'
                RETURNING {_COLUMNAS}
                """,
                {"id": archivo_id},
            ).fetchone()

    def fijar_retencion(self, archivo_id: str, desde: datetime) -> dict[str, Any] | None:
        """La primera fecha gana: pedir la retención dos veces no la corre hacia adelante."""
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                UPDATE archivos
                SET retener_desde = COALESCE(retener_desde, %(desde)s), actualizado_en = now()
                WHERE id = %(id)s AND estado = 'DISPONIBLE'
                RETURNING {_COLUMNAS}
                """,
                {"id": archivo_id, "desde": desde},
            ).fetchone()

    def vencidos(self, dias_por_tipo: dict[str, int], ahora: datetime, limite: int) -> list[dict]:
        """Los DISPONIBLE cuya retención ya venció, según los días de su tipo."""
        if not dias_por_tipo:
            return []
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM archivos a
                JOIN unnest(%(tipos)s::text[], %(dias)s::int[]) AS r(tipo, dias) ON r.tipo = a.tipo
                WHERE a.estado = 'DISPONIBLE' AND a.retener_desde IS NOT NULL
                  AND a.retener_desde + make_interval(days => r.dias) <= %(ahora)s
                ORDER BY a.retener_desde LIMIT %(limite)s
                """,
                {
                    "tipos": list(dias_por_tipo),
                    "dias": list(dias_por_tipo.values()),
                    "ahora": ahora,
                    "limite": limite,
                },
            ).fetchall()

    def registrar_acceso(
        self, archivo_id: str, accion: str, actor: str, actor_id: str | None, vence_en: datetime
    ) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                """
                INSERT INTO accesos_archivo (archivo_id, accion, actor, actor_id, vence_en)
                VALUES (%(archivo)s, %(accion)s, %(actor)s, %(actor_id)s, %(vence)s)
                """,
                {
                    "archivo": archivo_id,
                    "accion": accion,
                    "actor": actor,
                    "actor_id": actor_id,
                    "vence": vence_en,
                },
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
