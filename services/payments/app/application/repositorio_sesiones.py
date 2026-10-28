"""Sesiones de pago y medios de pago en PostgreSQL (HU-W03, W03-28 a W03-30; W05-A1 y A2).

Capa simple (regla 3). PAYMENTS es el **único escritor** de `sesiones_pago`, `medios_pago` y
`cobros` (regla 1). El esquema lo crea la migración `0008` (ADR-07).

- Abrir dos sesiones a la vez para la misma propuesta no crea dos: `(propuesta_id, intento)` es
  único y la segunda inserción devuelve la que ganó.
- `registrar_pago` guarda en **una transacción** el medio, el cobro `INTERACTIVO` y el cambio de la
  sesión a `cobrada`: o queda todo o nada. El cobro es idempotente por `id_intento_cobro` (el de la
  sesión), así que confirmar dos veces el mismo pago no crea otro cobro.
- El token del medio se guarda cifrado (con el id del medio como dato asociado) y el del cobro
  también, como en `repositorio_cobros.py`. Nunca el número ni los últimos dígitos."""

import uuid
from datetime import datetime
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos
from app.application.repositorio_cobros import _COLUMNAS as _COLUMNAS_COBRO

_COLUMNAS = (
    "id, propuesta_id, cliente_id, intento, id_intento_cobro, proveedor_sesion_id, estado, "
    "motivo, monto, moneda, url_pago, vence_en, fallos_seguidos, cobro_id, creado_en, "
    "actualizado_en"
)


class RepositorioSesiones(Protocol):
    def ultima(self, propuesta_id: str) -> dict | None: ...

    def por_proveedor(self, proveedor_sesion_id: str) -> dict | None: ...

    def crear(self, sesion: dict[str, Any]) -> dict: ...

    def actualizar(
        self, sesion_id: str, estado: str, motivo: str | None, fallos_seguidos: int
    ) -> dict: ...

    def registrar_pago(
        self, sesion: dict[str, Any], medio: dict[str, str], referencia: str | None
    ) -> tuple[dict, dict]: ...

    def medios(self, ids: list[str]) -> dict[str, dict[str, str]]: ...


class RepositorioSesionesPostgres:
    def __init__(self, pool: ConnectionPool, cifrador: CifradorCampos | None = None) -> None:
        self._pool = pool
        self._cifrador = cifrador

    def _cifrar(self, texto: str, dueno: str) -> str:
        return self._cifrador.cifrar(texto, dueno) if self._cifrador else texto

    def _descifrar_cobro(self, fila: dict) -> dict:
        cobro = dict(fila)
        if self._cifrador is not None:
            cobro["token_medio_pago"] = self._cifrador.descifrar(
                cobro["token_medio_pago"], cobro["id"]
            )
        return cobro

    def ultima(self, propuesta_id: str) -> dict | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM sesiones_pago WHERE propuesta_id = %(p)s
                ORDER BY intento DESC LIMIT 1
                """,
                {"p": propuesta_id},
            ).fetchone()

    def por_proveedor(self, proveedor_sesion_id: str) -> dict | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"SELECT {_COLUMNAS} FROM sesiones_pago WHERE proveedor_sesion_id = %(s)s",
                {"s": proveedor_sesion_id},
            ).fetchone()

    def crear(self, sesion: dict[str, Any]) -> dict:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                INSERT INTO sesiones_pago
                    (id, propuesta_id, cliente_id, intento, id_intento_cobro,
                     proveedor_sesion_id, estado, motivo, monto, moneda, url_pago, vence_en,
                     fallos_seguidos)
                VALUES
                    (%(id)s, %(propuesta_id)s, %(cliente_id)s, %(intento)s, %(id_intento_cobro)s,
                     %(proveedor_sesion_id)s, %(estado)s, %(motivo)s, %(monto)s, %(moneda)s,
                     %(url_pago)s, %(vence_en)s, %(fallos_seguidos)s)
                ON CONFLICT (propuesta_id, intento) DO NOTHING
                RETURNING {_COLUMNAS}
                """,
                sesion,
            ).fetchone()
            if fila is not None:
                return fila
            return conexion.execute(
                f"""
                SELECT {_COLUMNAS} FROM sesiones_pago
                WHERE propuesta_id = %(propuesta_id)s AND intento = %(intento)s
                """,
                sesion,
            ).fetchone()

    def actualizar(
        self, sesion_id: str, estado: str, motivo: str | None, fallos_seguidos: int
    ) -> dict:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"""
                UPDATE sesiones_pago
                SET estado = %(estado)s, motivo = %(motivo)s,
                    fallos_seguidos = %(fallos)s, actualizado_en = now()
                WHERE id = %(id)s
                RETURNING {_COLUMNAS}
                """,
                {"id": sesion_id, "estado": estado, "motivo": motivo, "fallos": fallos_seguidos},
            ).fetchone()

    def registrar_pago(
        self, sesion: dict[str, Any], medio: dict[str, str], referencia: str | None
    ) -> tuple[dict, dict]:
        medio_id = str(uuid.uuid4())
        cobro_id = str(uuid.uuid4())
        with self._pool.connection() as conexion, conexion.transaction():
            conexion.execute(
                """
                INSERT INTO medios_pago (id, propuesta_id, cliente_id, token_cifrado, marca, tipo)
                VALUES (%(id)s, %(propuesta_id)s, %(cliente_id)s, %(token)s, %(marca)s, %(tipo)s)
                """,
                {
                    "id": medio_id,
                    "propuesta_id": sesion["propuesta_id"],
                    "cliente_id": sesion["cliente_id"],
                    "token": self._cifrar(medio["token"], medio_id),
                    "marca": medio["marca"],
                    "tipo": medio["tipo"],
                },
            )
            cobro = conexion.execute(
                f"""
                INSERT INTO cobros
                    (id, id_intento_cobro, poliza_id, cliente_id, monto, moneda,
                     token_medio_pago, estado, referencia, origen, medio_pago_id)
                VALUES
                    (%(id)s, %(intento)s, %(poliza_id)s, %(cliente_id)s, %(monto)s, %(moneda)s,
                     %(token)s, 'cobrado', %(referencia)s, 'INTERACTIVO', %(medio)s)
                ON CONFLICT (id_intento_cobro) DO NOTHING
                RETURNING {_COLUMNAS_COBRO}
                """,
                {
                    "id": cobro_id,
                    "intento": sesion["id_intento_cobro"],
                    # Convención de POLICY: el cobro de una propuesta lleva su id como póliza.
                    "poliza_id": sesion["propuesta_id"],
                    "cliente_id": sesion["cliente_id"],
                    "monto": sesion["monto"],
                    "moneda": sesion["moneda"],
                    "token": self._cifrar(medio["token"], cobro_id),
                    "referencia": referencia,
                    "medio": medio_id,
                },
            ).fetchone()
            if cobro is None:
                cobro = conexion.execute(
                    f"SELECT {_COLUMNAS_COBRO} FROM cobros WHERE id_intento_cobro = %(i)s",
                    {"i": sesion["id_intento_cobro"]},
                ).fetchone()
            actualizada = conexion.execute(
                f"""
                UPDATE sesiones_pago
                SET estado = 'cobrada', motivo = NULL, fallos_seguidos = 0,
                    cobro_id = %(cobro)s, actualizado_en = now()
                WHERE id = %(id)s
                RETURNING {_COLUMNAS}
                """,
                {"id": sesion["id"], "cobro": cobro["id"]},
            ).fetchone()
        return actualizada, self._descifrar_cobro(cobro)

    def medios(self, ids: list[str]) -> dict[str, dict[str, str]]:
        """Marca y tipo de cada medio pedido (nunca el token), para la tabla de cobros."""
        if not ids:
            return {}
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                "SELECT id, marca, tipo FROM medios_pago WHERE id = ANY(%(ids)s)", {"ids": ids}
            ).fetchall()
        return {f["id"]: {"marca": f["marca"], "tipo": f["tipo"]} for f in filas}


def vence_en(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None
