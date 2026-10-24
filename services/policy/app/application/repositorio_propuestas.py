"""Persistencia de propuestas en PostgreSQL (HU-W03, KAN-209).

Capa simple (sin puertos y adaptadores: hexagonal es solo para `acl-worker`, regla #3). POLICY es el
**único escritor** de `propuestas` (regla #1).

**Idempotencia en la base de datos (CA-W03-01):** `oferta_id` es UNIQUE y `crear_o_obtener` hace
`INSERT ... ON CONFLICT (oferta_id) DO NOTHING RETURNING ...`. Con dos aceptaciones simultáneas de
la misma oferta, Postgres serializa el conflicto con el lock del índice y la segunda lee la fila de
la primera. El pool va en `autocommit` (una sentencia = una transacción) para que la garantía
dependa solo de ese lock.

La oferta (prima, cobertura, plazo) se guarda cifrada por campo (HA-SEG-002), con el id de la
propuesta como dato asociado. La evidencia de firma (`evidencias_firma`) es append-only en la base y
guarda el documento firmado cifrado, con el id de la evidencia como dato asociado."""

import json
import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos

_COLUMNAS = (
    "id, oferta_id, cliente_id, consentimiento_id, actor_usuario_id, estado, oferta_cifrada, "
    "intentos_emision, motivo_estado, creado_en, actualizado_en"
)
# La póliza de la propuesta, si ya se emitió (una subconsulta: `polizas.propuesta_id` es UNIQUE).
_POLIZA_ID = "(SELECT p.id FROM polizas p WHERE p.propuesta_id = propuestas.id) AS poliza_id"

_COLUMNAS_POLIZA = "id, propuesta_id, numero, cliente_id, estado, vigencia_desde, vigencia_hasta"

_COLUMNAS_EVIDENCIA = (
    "id, propuesta_id, documento_hash, version_documento, firmante_id, "
    "referencia_proveedor, firmada_en, creado_en"
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
                f"SELECT {_COLUMNAS}, {_POLIZA_ID} FROM propuestas WHERE id = %(id)s",
                {"id": propuesta_id},
            ).fetchone()
        return self._a_propuesta(fila) if fila else None

    def obtener_por_oferta(self, oferta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS}, {_POLIZA_ID} FROM propuestas WHERE oferta_id = %(oferta_id)s",
                {"oferta_id": oferta_id},
            ).fetchone()
        return self._a_propuesta(fila) if fila else None

    def crear_o_obtener(self, propuesta_id: str, datos: dict[str, Any]) -> tuple[dict, bool]:
        """Devuelve la propuesta y si se creó ahora (False = ya existía para esa oferta)."""
        oferta = {k: datos[k] for k in ("tipo", "prima", "cobertura", "plazo_meses")}
        # El nivel de riesgo de la oferta lo necesita UNDER al decidir (puede faltar).
        oferta["nivel_riesgo"] = datos.get("nivel_riesgo")
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
                RETURNING {_COLUMNAS}, NULL::text AS poliza_id
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
                f"SELECT {_COLUMNAS}, {_POLIZA_ID} FROM propuestas WHERE oferta_id = %(oferta_id)s",
                {"oferta_id": datos["oferta_id"]},
            ).fetchone()
        return self._a_propuesta(existente), False

    def registrar_intento(self, propuesta_id: str) -> int:
        """Cuenta un intento de emisión y devuelve el total (acota los reintentos del cobro)."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "UPDATE propuestas SET intentos_emision = intentos_emision + 1 "
                "WHERE id = %(id)s RETURNING intentos_emision",
                {"id": propuesta_id},
            ).fetchone()
        return fila["intentos_emision"]

    def cambiar_estado(
        self, propuesta_id: str, nuevo: str, desde: tuple[str, ...], motivo: str | None = None
    ) -> bool:
        """Cambia el estado solo si sigue en uno de `desde`: dos emisiones a la vez no se pisan.
        Devuelve si cambió."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "UPDATE propuestas SET estado = %(nuevo)s, motivo_estado = %(motivo)s, "
                "actualizado_en = now() WHERE id = %(id)s AND estado = ANY(%(desde)s) RETURNING id",
                {"id": propuesta_id, "nuevo": nuevo, "motivo": motivo, "desde": list(desde)},
            ).fetchone()
        return fila is not None

    def emitir_poliza(
        self, propuesta_id: str, cliente_id: str, vigencia_desde: Any, vigencia_hasta: Any
    ) -> tuple[dict[str, Any], bool]:
        """Pasa la propuesta a `EMITIDA` y crea su póliza, todo en una transacción. Devuelve la
        póliza y si se creó ahora (False = ya existía: emitir dos veces no
        duplica). Si la propuesta ya no está en un estado emisible (p. ej. otra emisión la movió),
        devuelve la póliza existente o `None`."""
        with self._pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(
                "UPDATE propuestas SET estado = 'EMITIDA', motivo_estado = NULL, "
                "actualizado_en = now() WHERE id = %(id)s "
                "AND estado IN ('EN_EMISION', 'PENDIENTE_PAGO') RETURNING id",
                {"id": propuesta_id},
            ).fetchone()
            if fila is None:
                existente = conexion.execute(
                    f"SELECT {_COLUMNAS_POLIZA} FROM polizas WHERE propuesta_id = %(id)s",
                    {"id": propuesta_id},
                ).fetchone()
                return existente, False
            poliza = conexion.execute(
                f"""
                INSERT INTO polizas
                    (id, propuesta_id, numero, cliente_id, vigencia_desde, vigencia_hasta)
                VALUES
                    (%(id)s, %(propuesta_id)s, nextval('polizas_numero_seq'), %(cliente_id)s,
                     %(vigencia_desde)s, %(vigencia_hasta)s)
                RETURNING {_COLUMNAS_POLIZA}
                """,
                {
                    "id": str(uuid.uuid4()),
                    "propuesta_id": propuesta_id,
                    "cliente_id": cliente_id,
                    "vigencia_desde": vigencia_desde,
                    "vigencia_hasta": vigencia_hasta,
                },
            ).fetchone()
        return poliza, True

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None:
        """La póliza con la prima, la cobertura y el dueño de su propuesta."""
        with self._pool.connection() as conexion:
            poliza = conexion.execute(
                f"SELECT {_COLUMNAS_POLIZA} FROM polizas WHERE id = %(id)s", {"id": poliza_id}
            ).fetchone()
        if poliza is None:
            return None
        propuesta = self.obtener(poliza["propuesta_id"])
        return {
            **poliza,
            "prima": propuesta["prima"],
            "cobertura": propuesta["cobertura"],
            "actor_usuario_id": propuesta["actor_usuario_id"],
        }

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"SELECT {_COLUMNAS_EVIDENCIA} FROM evidencias_firma "
                "WHERE propuesta_id = %(propuesta_id)s",
                {"propuesta_id": propuesta_id},
            ).fetchone()
        return fila

    def registrar_firma(self, propuesta_id: str, evidencia: dict[str, Any]) -> tuple[dict, bool]:
        """Guarda la evidencia (una sola vez por propuesta) y pasa la propuesta a `EN_EMISION`,
        todo en una transacción: nunca queda firmada sin evidencia ni con evidencia sin estado.
        Devuelve la evidencia y si se creó ahora (False = otra petición ya la había guardado)."""
        documento = self._cifrador.cifrar(evidencia["documento"], evidencia["id"])
        with self._pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(
                f"""
                INSERT INTO evidencias_firma
                    (id, propuesta_id, documento_hash, version_documento, documento_cifrado,
                     firmante_id, consentimiento_id, referencia_proveedor, firmada_en)
                VALUES
                    (%(id)s, %(propuesta_id)s, %(documento_hash)s, %(version_documento)s,
                     %(documento_cifrado)s, %(firmante_id)s, %(consentimiento_id)s,
                     %(referencia_proveedor)s, %(firmada_en)s)
                ON CONFLICT (propuesta_id) DO NOTHING
                RETURNING {_COLUMNAS_EVIDENCIA}
                """,
                {
                    **{k: v for k, v in evidencia.items() if k != "documento"},
                    "propuesta_id": propuesta_id,
                    "documento_cifrado": documento,
                },
            ).fetchone()
            creada = fila is not None
            if creada:
                conexion.execute(
                    "UPDATE propuestas SET estado = 'EN_EMISION', actualizado_en = now() "
                    "WHERE id = %(id)s AND estado = 'ACEPTADA'",
                    {"id": propuesta_id},
                )
            else:
                fila = conexion.execute(
                    f"SELECT {_COLUMNAS_EVIDENCIA} FROM evidencias_firma "
                    "WHERE propuesta_id = %(propuesta_id)s",
                    {"propuesta_id": propuesta_id},
                ).fetchone()
        return fila, creada


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
