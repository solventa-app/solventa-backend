"""Persistencia de cobros en PostgreSQL (T-W05-3).

Capa simple (sin puertos y adaptadores formales — regla de arquitectura #3:
hexagonal es solo para `acl-worker`). PAYMENTS es el **único escritor** de
la tabla `cobros` (regla de arquitectura #1): este módulo es el único lugar
del sistema que escribe ahí.

**Idempotencia real a nivel de base de datos (CA-W05-04):** `id_intento_cobro`
tiene un índice UNIQUE (creado por la migración `0002_cobros`, ver
`migrations/versions/0002_cobros.py` — el esquema lo crea Alembic, no esta
clase: nada de migrar al arrancar la app, ADR-07). `crear_o_obtener` hace
un `INSERT ... ON CONFLICT (id_intento_cobro) DO NOTHING RETURNING ...`.
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

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos

_COLUMNAS = (
    "id, id_intento_cobro, poliza_id, cliente_id, monto, moneda, token_medio_pago, "
    "estado, referencia, motivo, intentos, reintentos_rechazo, referencia_pasarela, "
    "origen, medio_pago_id, creado_en, actualizado_en"
)


class RepositorioCobrosPostgres:
    """`cifrador`: el `token_medio_pago` se guarda cifrado (HA-SEG-002, EC022) y se descifra al
    leer, así que quien usa el repositorio siempre ve el token en claro. Se omite solo en
    pruebas."""

    def __init__(self, pool: ConnectionPool, cifrador: CifradorCampos | None = None) -> None:
        self._pool = pool
        self._cifrador = cifrador

    def _a_cobro(self, fila: dict) -> dict:
        cobro = dict(fila)
        if self._cifrador is not None:
            cobro["token_medio_pago"] = self._cifrador.descifrar(
                cobro["token_medio_pago"], cobro["id"]
            )
        return cobro

    def crear_o_obtener(self, cobro_id: str, orden: dict) -> tuple[dict, bool]:
        token = orden["token_medio_pago"]
        if self._cifrador is not None:
            token = self._cifrador.cifrar(token, cobro_id)
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                INSERT INTO cobros
                    (id, id_intento_cobro, poliza_id, cliente_id, monto, moneda,
                     token_medio_pago, estado)
                VALUES
                    (%(id)s, %(id_intento_cobro)s, %(poliza_id)s, %(cliente_id)s, %(monto)s,
                     %(moneda)s, %(token_medio_pago)s, 'cobrando')
                ON CONFLICT (id_intento_cobro) DO NOTHING
                RETURNING {_COLUMNAS}
                """,
                {"id": cobro_id, "cliente_id": None, **orden, "token_medio_pago": token},
            ).fetchone()
            if fila is not None:
                return self._a_cobro(fila), True

            fila_existente = conexion.execute(
                f"SELECT {_COLUMNAS} FROM cobros WHERE id_intento_cobro = %(id_intento_cobro)s",
                {"id_intento_cobro": orden["id_intento_cobro"]},
            ).fetchone()
            return self._a_cobro(fila_existente), False

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
            return self._a_cobro(fila)

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
            return self._a_cobro(fila)

    def iniciar_reintento_rechazo(self, cobro_id: str, maximo: int) -> dict | None:
        """Reintento manual de un `rechazado` (CA-W05-02). En una sola sentencia: lo pasa a
        `cobrando`, suma un reintento y le da su propia referencia ante la pasarela
        (`{id_intento_cobro}-rN`). Si el cobro ya no está `rechazado` (otro clic ganó) o agotó
        `maximo`, no toca nada y devuelve `None`: dos clics a la vez no cobran dos veces."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                UPDATE cobros
                SET estado = 'cobrando',
                    reintentos_rechazo = reintentos_rechazo + 1,
                    referencia_pasarela = id_intento_cobro || '-r' || (reintentos_rechazo + 1),
                    referencia = NULL, motivo = NULL, actualizado_en = now()
                WHERE id = %(id)s AND estado = 'rechazado' AND reintentos_rechazo < %(maximo)s
                RETURNING {_COLUMNAS}
                """,
                {"id": cobro_id, "maximo": maximo},
            ).fetchone()
            return self._a_cobro(fila) if fila is not None else None

    def listar_pendientes(self, max_intentos: int) -> list[dict]:
        """Los `pendiente` que el reintento de fondo debe volver a cobrar. Nunca los
        `INTERACTIVO` (W05-A3): esos los paga el titular en la página de la pasarela y solo se
        concilian al confirmar la sesión."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM cobros
                WHERE estado = 'pendiente' AND intentos < %(max_intentos)s
                  AND origen <> 'INTERACTIVO'
                ORDER BY creado_en ASC
                """,
                {"max_intentos": max_intentos},
            ).fetchall()
            return [self._a_cobro(fila) for fila in filas]

    def listar_atascados(self, minutos: float) -> list[dict]:
        """Cobros que quedaron en `cobrando` porque el proceso se cayó a mitad del cobro: ya pasaron
        más de `minutos` desde su último cambio, mucho más de lo que tarda una llamada al ACL."""
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM cobros
                WHERE estado = 'cobrando' AND origen <> 'INTERACTIVO'
                  AND actualizado_en < now() - make_interval(secs => %(segundos)s)
                ORDER BY creado_en ASC
                """,
                {"segundos": minutos * 60},
            ).fetchall()
            return [self._a_cobro(fila) for fila in filas]

    def obtener(self, cobro_id: str) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS} FROM cobros WHERE id = %(id)s", {"id": cobro_id}
            ).fetchone()
            return self._a_cobro(fila) if fila is not None else None

    def listar(
        self,
        poliza_id: str | None,
        estado: str | None,
        pagina: int | None = None,
        tamano: int | None = None,
    ) -> list[dict]:
        """Para la tabla del back-office (CA-W05-01); ambos filtros son opcionales."""
        query = f"""
            SELECT {_COLUMNAS} FROM cobros
            WHERE (%(poliza_id)s::text IS NULL OR poliza_id = %(poliza_id)s)
              AND (%(estado)s::text IS NULL OR estado = %(estado)s)
            ORDER BY actualizado_en DESC
        """
        parametros: dict[str, object] = {"poliza_id": poliza_id, "estado": estado}
        if pagina is not None and tamano is not None and pagina > 0 and tamano > 0:
            query += " LIMIT %(limit)s OFFSET %(offset)s"
            parametros["limit"] = tamano
            parametros["offset"] = (pagina - 1) * tamano

        with self._pool.connection() as conexion:
            filas = conexion.execute(query, parametros).fetchall()
            return [self._a_cobro(fila) for fila in filas]

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
    if not database_url:
        raise ValueError("Falta DATABASE_URL")
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
