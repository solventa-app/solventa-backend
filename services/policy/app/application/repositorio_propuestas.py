"""Persistencia de propuestas en PostgreSQL (HU-W03, KAN-209).

Capa simple (sin puertos y adaptadores: hexagonal es solo para `acl-worker`, regla #3). POLICY es el
**único escritor** de `propuestas` (regla #1).

**Idempotencia en la base de datos (CA-W03-01):** `oferta_id` es UNIQUE y `crear_o_obtener` hace
`INSERT ... ON CONFLICT (oferta_id) DO NOTHING RETURNING ...`. Con dos aceptaciones simultáneas de
la misma oferta, Postgres serializa el conflicto con el lock del índice y la segunda lee la fila de
la primera. El pool va en `autocommit` (una sentencia = una transacción) para que la garantía
dependa solo de ese lock.

La oferta (prima, cobertura, plazo) se guarda cifrada por campo (HA-SEG-002), con el id de la
propuesta como dato asociado."""

import json
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos

_COLUMNAS = (
    "id, oferta_id, cliente_id, consentimiento_id, actor_usuario_id, estado, oferta_cifrada, "
    "creado_en, actualizado_en"
)


class RepositorioPropuestasPostgres:
    def __init__(self, pool: ConnectionPool, cifrador: CifradorCampos) -> None:
        self._pool = pool
        self._cifrador = cifrador

    def _a_propuesta(self, fila: dict[str, Any]) -> dict[str, Any]:
        propuesta = {k: v for k, v in fila.items() if k != "oferta_cifrada"}
        propuesta.update(json.loads(self._cifrador.descifrar(fila["oferta_cifrada"], fila["id"])))
        return propuesta

    def obtener(self, propuesta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS} FROM propuestas WHERE id = %(id)s", {"id": propuesta_id}
            ).fetchone()
        return self._a_propuesta(fila) if fila else None

    def obtener_por_oferta(self, oferta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS} FROM propuestas WHERE oferta_id = %(oferta_id)s",
                {"oferta_id": oferta_id},
            ).fetchone()
        return self._a_propuesta(fila) if fila else None

    def crear_o_obtener(self, propuesta_id: str, datos: dict[str, Any]) -> tuple[dict, bool]:
        """Devuelve la propuesta y si se creó ahora (False = ya existía para esa oferta)."""
        oferta = {k: datos[k] for k in ("tipo", "prima", "cobertura", "plazo_meses")}
        cifrada = self._cifrador.cifrar(json.dumps(oferta), propuesta_id)
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                INSERT INTO propuestas
                    (id, oferta_id, cliente_id, consentimiento_id, actor_usuario_id, estado,
                     oferta_cifrada)
                VALUES
                    (%(id)s, %(oferta_id)s, %(cliente_id)s, %(consentimiento_id)s,
                     %(actor_usuario_id)s, 'ACEPTADA', %(oferta_cifrada)s)
                ON CONFLICT (oferta_id) DO NOTHING
                RETURNING {_COLUMNAS}
                """,
                {
                    "id": propuesta_id,
                    "oferta_id": datos["oferta_id"],
                    "cliente_id": datos["cliente_id"],
                    "consentimiento_id": datos.get("consentimiento_id"),
                    "actor_usuario_id": datos.get("actor_usuario_id"),
                    "oferta_cifrada": cifrada,
                },
            ).fetchone()
            if fila is not None:
                return self._a_propuesta(fila), True
            existente = conexion.execute(
                f"SELECT {_COLUMNAS} FROM propuestas WHERE oferta_id = %(oferta_id)s",
                {"oferta_id": datos["oferta_id"]},
            ).fetchone()
        return self._a_propuesta(existente), False


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
