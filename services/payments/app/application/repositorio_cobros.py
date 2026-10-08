"""Persistencia de cobros en PostgreSQL (T-W05-3).

Capa simple (sin puertos y adaptadores formales — regla de arquitectura #3:
hexagonal es solo para `acl-worker`). PAYMENTS es el **único escritor** de
la tabla `cobros` (regla de arquitectura #1): este módulo es el único lugar
del sistema que escribe ahí.

**Idempotencia real a nivel de base de datos (CA-W05-04):** `clave_idempotencia`
tiene un índice UNIQUE (ver `app/sql/001_cobros.sql`). `crear_o_obtener` hace
un `INSERT ... ON CONFLICT (clave_idempotencia) DO NOTHING RETURNING ...`.
Bajo concurrencia real (dos conexiones insertando la MISMA clave al mismo
tiempo), Postgres serializa el conflicto con el lock del índice único: la
segunda conexión en llegar se bloquea hasta que la primera termine su
transacción (commit o rollback) y solo entonces evalúa el `ON CONFLICT` — así
que si el `RETURNING` no devuelve fila, significa que la otra transacción ya
confirmó y la fila ya es visible. El `SELECT` que sigue en ese caso no tiene
ninguna carrera con el INSERT que ganó: lee una fila que ya está committeada.
Se usa `autocommit=True` en el pool (una sentencia = una transacción) para
que esa garantía dependa solo del lock de Postgres, no de un manejo manual de
transacciones en Python."""

from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

RUTA_ESQUEMA = Path(__file__).resolve().parents[1] / "sql" / "001_cobros.sql"

_COLUMNAS = (
    "id, clave_idempotencia, poliza_id, monto, moneda, token_medio_pago, "
    "estado, referencia, motivo, intentos"
)


class RepositorioCobrosPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def preparar_esquema(self) -> None:
        esquema_sql = RUTA_ESQUEMA.read_text(encoding="utf-8")
        with self._pool.connection() as conexion:
            conexion.execute(esquema_sql)

    def crear_o_obtener(self, cobro_id: str, orden: dict) -> tuple[dict, bool]:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                INSERT INTO cobros
                    (id, clave_idempotencia, poliza_id, monto, moneda, token_medio_pago, estado)
                VALUES
                    (%(id)s, %(clave_idempotencia)s, %(poliza_id)s, %(monto)s, %(moneda)s,
                     %(token_medio_pago)s, 'cobrando')
                ON CONFLICT (clave_idempotencia) DO NOTHING
                RETURNING {_COLUMNAS}
                """,
                {"id": cobro_id, **orden},
            ).fetchone()
            if fila is not None:
                return dict(fila), True

            fila_existente = conexion.execute(
                f"SELECT {_COLUMNAS} FROM cobros WHERE clave_idempotencia = %(clave_idempotencia)s",
                {"clave_idempotencia": orden["clave_idempotencia"]},
            ).fetchone()
            return dict(fila_existente), False

    def actualizar_estado(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                UPDATE cobros
                SET estado = %(estado)s, referencia = %(referencia)s, motivo = %(motivo)s,
                    actualizado_en = now()
                WHERE id = %(id)s
                RETURNING {_COLUMNAS}
                """,
                {"id": cobro_id, "estado": estado, "referencia": referencia, "motivo": motivo},
            ).fetchone()
            return dict(fila)

    def actualizar_estado_y_contar_intento(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict:
        """Usado solo por el reintento (T-W05-4): además de actualizar el
        estado, incrementa `intentos` para que `listar_pendientes` deje de
        devolver el cobro una vez agotado `max_intentos`."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                UPDATE cobros
                SET estado = %(estado)s, referencia = %(referencia)s, motivo = %(motivo)s,
                    intentos = intentos + 1, actualizado_en = now()
                WHERE id = %(id)s
                RETURNING {_COLUMNAS}
                """,
                {"id": cobro_id, "estado": estado, "referencia": referencia, "motivo": motivo},
            ).fetchone()
            return dict(fila)

    def listar_pendientes(self, max_intentos: int) -> list[dict]:
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM cobros
                WHERE estado = 'pendiente' AND intentos < %(max_intentos)s
                ORDER BY creado_en ASC
                """,
                {"max_intentos": max_intentos},
            ).fetchall()
            return [dict(fila) for fila in filas]

    def balance_poliza(self, poliza_id: str) -> str:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                """
                SELECT COALESCE(SUM(monto::numeric), 0)::text AS total
                FROM cobros
                WHERE poliza_id = %(poliza_id)s AND estado = 'cobrado'
                """,
                {"poliza_id": poliza_id},
            ).fetchone()
            return fila["total"]


def construir_pool(database_url: str) -> ConnectionPool:
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
