"""Registro de consentimientos sobre PostgreSQL (T-W01-4, HA-SEG-001, CA-W01-02/08, bloque 4).

Tabla `consentimientos` de la base `auth` (migraciones `0004` y `0005`). **Append-only**: revocar
fija `revocado_en` una sola vez y nunca se borra ni se alteran sus demás columnas; lo hace cumplir
un trigger en la base, no solo este código. El vencimiento (`vence_en`, 30 días por defecto,
decisión 23) se fija al otorgarlo y se evalúa al leer (`now() < vence_en`), sin cron.

Pendiente (ver `docs/sprint-1.md`): emitir `consentimiento.registrado`/`consentimiento.revocado`
cuando exista Pub/Sub."""

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

VIGENCIA_DIAS_POR_DEFECTO = 30


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
    vence_en: str = ""
    titular_id: str | None = None
    tipo_dato: str | None = None
    fuente_datos: str | None = None
    canal: str | None = None

    def __post_init__(self) -> None:
        if not self.vence_en and self.otorgado_en:
            otorgado = datetime.fromisoformat(self.otorgado_en)
            if otorgado.tzinfo is None:
                otorgado = otorgado.replace(tzinfo=UTC)
            self.vence_en = (otorgado + timedelta(days=VIGENCIA_DIAS_POR_DEFECTO)).isoformat()

    @property
    def estado(self) -> str:
        if self.revocado_en is not None:
            return "REVOCADO"
        vence = datetime.fromisoformat(self.vence_en)
        if vence.tzinfo is None:
            vence = vence.replace(tzinfo=UTC)
        if datetime.now(UTC) >= vence:
            return "VENCIDO"
        return "VIGENTE"

    @property
    def vigente(self) -> bool:
        return self.estado == "VIGENTE"

    def como_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "vigente": self.vigente,
            "estado": self.estado,
            "vence_en": self.vence_en,
            "titular_id": self.titular_id,
            "tipo_dato": self.tipo_dato,
            "fuente_datos": self.fuente_datos,
            "canal": self.canal,
        }


class AlmacenConsentimientos(Protocol):
    def registrar(
        self,
        finalidad: str,
        version_texto: str,
        actor: dict[str, str] | None = None,
        titular_id: str | None = None,
        tipo_dato: str | None = None,
        fuente_datos: str | None = None,
        canal: str | None = None,
        vigencia_dias: int = VIGENCIA_DIAS_POR_DEFECTO,
    ) -> Consentimiento: ...

    def obtener(self, consentimiento_id: str) -> Consentimiento | None: ...

    def revocar(self, consentimiento_id: str) -> Consentimiento | None: ...


_COLUMNAS = (
    "id, finalidad, version_texto, otorgado_en, revocado_en, "
    "actor_rol, actor_usuario_id, titular_id, tipo_dato, fuente_datos, canal, vence_en"
)


def _desde_fila(fila: dict[str, Any]) -> Consentimiento:
    actor = (
        {"rol": fila["actor_rol"], "usuario_id": fila["actor_usuario_id"]}
        if fila.get("actor_rol") is not None
        else None
    )
    revocado_en: datetime | None = fila.get("revocado_en")
    vence_en_raw = fila.get("vence_en")
    if isinstance(vence_en_raw, datetime):
        vence_en = vence_en_raw.isoformat()
    elif isinstance(vence_en_raw, str) and vence_en_raw:
        vence_en = vence_en_raw
    else:
        vence_en = ""
    return Consentimiento(
        id=fila["id"],
        finalidad=fila["finalidad"],
        version_texto=fila["version_texto"],
        otorgado_en=fila["otorgado_en"].isoformat(),
        revocado_en=revocado_en.isoformat() if revocado_en is not None else None,
        actor=actor,
        vence_en=vence_en,
        titular_id=fila.get("titular_id"),
        tipo_dato=fila.get("tipo_dato"),
        fuente_datos=fila.get("fuente_datos"),
        canal=fila.get("canal"),
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
        self,
        finalidad: str,
        version_texto: str,
        actor: dict[str, str] | None = None,
        titular_id: str | None = None,
        tipo_dato: str | None = None,
        fuente_datos: str | None = None,
        canal: str | None = None,
        vigencia_dias: int = VIGENCIA_DIAS_POR_DEFECTO,
    ) -> Consentimiento:
        consentimiento = self._una(
            f"""
            INSERT INTO consentimientos (
                id, finalidad, version_texto, actor_rol, actor_usuario_id,
                titular_id, tipo_dato, fuente_datos, canal, vence_en
            )
            VALUES (
                %(id)s, %(finalidad)s, %(version_texto)s, %(actor_rol)s, %(actor_usuario_id)s,
                %(titular_id)s, %(tipo_dato)s, %(fuente_datos)s, %(canal)s,
                now() + make_interval(days => %(vigencia_dias)s)
            )
            RETURNING {_COLUMNAS}
            """,
            {
                "id": str(uuid.uuid4()),
                "finalidad": finalidad,
                "version_texto": version_texto,
                "actor_rol": actor["rol"] if actor else None,
                "actor_usuario_id": actor["usuario_id"] if actor else None,
                "titular_id": titular_id,
                "tipo_dato": tipo_dato,
                "fuente_datos": fuente_datos,
                "canal": canal,
                "vigencia_dias": vigencia_dias,
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
