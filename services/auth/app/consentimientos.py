"""Registro de consentimientos — **MOCK EN MEMORIA**.

Existe solo para que el BFF pueda integrarse por HTTP con AUTH mientras se
construye T-W01-4 (tabla append-only en PostgreSQL, migración Alembic
`0002`). Cuando llegue, se reemplaza `AlmacenConsentimientosMemoria` por un
almacén sobre Postgres con la misma interfaz y los endpoints de `app/main.py`
no cambian (tampoco el BFF).

Limitaciones conocidas y aceptadas del mock:
- El estado se pierde al reiniciar el proceso.
- No se comparte entre instancias: en Cloud Run con más de una instancia, un
  consentimiento creado en una no existe en la otra.
- No emite `consentimiento.registrado`/`consentimiento.revocado`.

Semántica append-only igual que la definitiva: revocar marca `revocado_en`,
nunca borra."""

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass
class Consentimiento:
    id: str
    finalidad: str
    version_texto: str
    otorgado_en: str
    revocado_en: str | None = None

    @property
    def vigente(self) -> bool:
        return self.revocado_en is None

    def como_dict(self) -> dict[str, Any]:
        return {**asdict(self), "vigente": self.vigente}


class AlmacenConsentimientos(Protocol):
    def registrar(self, finalidad: str, version_texto: str) -> Consentimiento: ...

    def obtener(self, consentimiento_id: str) -> Consentimiento | None: ...

    def revocar(self, consentimiento_id: str) -> Consentimiento | None: ...


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class AlmacenConsentimientosMemoria:
    def __init__(self) -> None:
        self._por_id: dict[str, Consentimiento] = {}
        self._lock = threading.Lock()

    def registrar(self, finalidad: str, version_texto: str) -> Consentimiento:
        consentimiento = Consentimiento(
            id=str(uuid.uuid4()),
            finalidad=finalidad,
            version_texto=version_texto,
            otorgado_en=_ahora(),
        )
        with self._lock:
            self._por_id[consentimiento.id] = consentimiento
        return consentimiento

    def obtener(self, consentimiento_id: str) -> Consentimiento | None:
        with self._lock:
            return self._por_id.get(consentimiento_id)

    def revocar(self, consentimiento_id: str) -> Consentimiento | None:
        """Idempotente: revocar dos veces conserva la fecha de la primera."""
        with self._lock:
            consentimiento = self._por_id.get(consentimiento_id)
            if consentimiento is not None and consentimiento.revocado_en is None:
                consentimiento.revocado_en = _ahora()
            return consentimiento
