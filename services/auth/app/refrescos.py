"""Refresh tokens (T22): opacos, guardados solo como hash, con rotación en cada uso.

Un refresh es una cadena aleatoria de 256 bits; en la base solo vive su SHA-256. Cada uso emite
otro de la misma `familia_id` y deja el anterior con `usado_en`. Cada refresh queda atado al canal
(`app` o `web`) con que se emitió."""

import hashlib
import secrets
from datetime import datetime
from typing import Protocol

from psycopg_pool import ConnectionPool


def nuevo_refresh() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh(refresh: str) -> str:
    return hashlib.sha256(refresh.encode("utf-8")).hexdigest()


class RepositorioRefrescos(Protocol):
    def crear(
        self,
        refresh_id: str,
        familia_id: str,
        usuario_id: str,
        hash_token: str,
        canal: str,
        expira_en: datetime,
    ) -> None: ...

    def obtener_por_hash(self, hash_token: str) -> dict | None: ...

    def marcar_usado(self, refresh_id: str, ahora: datetime) -> bool:
        """Atómico: `True` solo para quien lo marca primero (dos usos simultáneos no pasan)."""
        ...

    def revocar_familia(self, familia_id: str, ahora: datetime) -> None: ...

    def revocar_de_usuario(self, usuario_id: str, ahora: datetime) -> None: ...


class RepositorioRefrescosPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def crear(
        self,
        refresh_id: str,
        familia_id: str,
        usuario_id: str,
        hash_token: str,
        canal: str,
        expira_en: datetime,
    ) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                """
                INSERT INTO refresh_tokens
                    (id, familia_id, usuario_id, hash_token, canal, expira_en)
                VALUES (%(id)s, %(familia)s, %(usuario)s, %(hash)s, %(canal)s, %(expira)s)
                """,
                {
                    "id": refresh_id,
                    "familia": familia_id,
                    "usuario": usuario_id,
                    "hash": hash_token,
                    "canal": canal,
                    "expira": expira_en,
                },
            )

    def obtener_por_hash(self, hash_token: str) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "SELECT id, familia_id, usuario_id, canal, expira_en, usado_en, revocado_en "
                "FROM refresh_tokens WHERE hash_token = %(h)s",
                {"h": hash_token},
            ).fetchone()
            return dict(fila) if fila is not None else None

    def marcar_usado(self, refresh_id: str, ahora: datetime) -> bool:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "UPDATE refresh_tokens SET usado_en = %(a)s "
                "WHERE id = %(id)s AND usado_en IS NULL AND revocado_en IS NULL RETURNING id",
                {"id": refresh_id, "a": ahora},
            ).fetchone()
            return fila is not None

    def revocar_familia(self, familia_id: str, ahora: datetime) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE refresh_tokens SET revocado_en = %(a)s "
                "WHERE familia_id = %(f)s AND revocado_en IS NULL",
                {"f": familia_id, "a": ahora},
            )

    def revocar_de_usuario(self, usuario_id: str, ahora: datetime) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE refresh_tokens SET revocado_en = %(a)s "
                "WHERE usuario_id = %(u)s AND revocado_en IS NULL",
                {"u": usuario_id, "a": ahora},
            )


__all__ = [
    "RepositorioRefrescos",
    "RepositorioRefrescosPostgres",
    "hash_refresh",
    "nuevo_refresh",
]
