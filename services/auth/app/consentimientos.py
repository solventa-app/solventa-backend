"""Registro de consentimientos sobre PostgreSQL (T-W01-4, HA-SEG-001, CA-W01-02/08).

Tabla `consentimientos` de la base `auth` (migración `0004`). **Append-only**: revocar fija
`revocado_en` una sola vez y nunca se borra; lo hace cumplir un trigger en la base, no solo
este código. El contrato HTTP de `app/main.py` no cambió respecto del mock en memoria que
había antes (ni el BFF): ese mock vive ahora en `tests/fakes.py` como doble de prueba.

Pendiente (ver `docs/sprint-1.md`): emitir `consentimiento.registrado`/`consentimiento.revocado`
cuando exista Pub/Sub."""

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from psycopg_pool import ConnectionPool


@dataclass
class Consentimiento:
    id: str
    finalidad: str
    version_texto: str
    otorgado_en: str
    revocado_en: str | None = None
    # Quién otorgó (CA-W01-02, CA-W01-11): `{"rol": ..., "usuario_id": ...}` con el id
    # opaco del usuario autenticado, o `None` si se registró sin sesión.
    actor: dict[str, str] | None = None

    @property
    def vigente(self) -> bool:
        return self.revocado_en is None

    def como_dict(self) -> dict[str, Any]:
        return {**asdict(self), "vigente": self.vigente}


class AlmacenConsentimientos(Protocol):
    def registrar(
        self, finalidad: str, version_texto: str, actor: dict[str, str] | None = None
    ) -> Consentimiento: ...

    def obtener(self, consentimiento_id: str) -> Consentimiento | None: ...

    def revocar(self, consentimiento_id: str) -> Consentimiento | None: ...


_COLUMNAS = "id, finalidad, version_texto, otorgado_en, revocado_en, actor_rol, actor_usuario_id"


def _desde_fila(fila: dict[str, Any]) -> Consentimiento:
    actor = (
        {"rol": fila["actor_rol"], "usuario_id": fila["actor_usuario_id"]}
        if fila["actor_rol"] is not None
        else None
    )
    revocado_en: datetime | None = fila["revocado_en"]
    return Consentimiento(
        id=fila["id"],
        finalidad=fila["finalidad"],
        version_texto=fila["version_texto"],
        otorgado_en=fila["otorgado_en"].isoformat(),
        revocado_en=revocado_en.isoformat() if revocado_en is not None else None,
        actor=actor,
    )


class AlmacenConsentimientosPostgres:
    """Cada sentencia es una transacción (`autocommit`), igual que `RepositorioUsuariosPostgres`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _una(self, sql: str, parametros: dict[str, Any]) -> Consentimiento | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(sql, parametros).fetchone()
            return _desde_fila(fila) if fila is not None else None

    def registrar(
        self, finalidad: str, version_texto: str, actor: dict[str, str] | None = None
    ) -> Consentimiento:
        consentimiento = self._una(
            f"""
            INSERT INTO consentimientos (id, finalidad, version_texto, actor_rol, actor_usuario_id)
            VALUES (%(id)s, %(finalidad)s, %(version_texto)s, %(actor_rol)s, %(actor_usuario_id)s)
            RETURNING {_COLUMNAS}
            """,
            {
                "id": str(uuid.uuid4()),
                "finalidad": finalidad,
                "version_texto": version_texto,
                "actor_rol": actor["rol"] if actor else None,
                "actor_usuario_id": actor["usuario_id"] if actor else None,
            },
        )
        assert consentimiento is not None  # INSERT ... RETURNING siempre devuelve la fila
        return consentimiento

    def obtener(self, consentimiento_id: str) -> Consentimiento | None:
        return self._una(
            f"SELECT {_COLUMNAS} FROM consentimientos WHERE id = %(id)s", {"id": consentimiento_id}
        )

    def revocar(self, consentimiento_id: str) -> Consentimiento | None:
        """Idempotente: revocar dos veces conserva la fecha de la primera."""
        revocado = self._una(
            f"""
            UPDATE consentimientos SET revocado_en = now()
            WHERE id = %(id)s AND revocado_en IS NULL
            RETURNING {_COLUMNAS}
            """,
            {"id": consentimiento_id},
        )
        return revocado if revocado is not None else self.obtener(consentimiento_id)
