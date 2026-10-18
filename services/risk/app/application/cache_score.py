"""Cache-Aside del score en RISK (Bloque 2, HA-LAT-001).

RISK calcula y guarda el `ScoreCacheado` en Redis con clave estable por cliente
y versión de reglas (`score:{id_opaco(cliente_id)}:{version_reglas}` y
`oferta:{id_opaco(cliente_id)}:{perfil_version}:{version_reglas}`) y TTL
configurable (`SCORE_CACHE_TTL_SEGUNDOS`, por defecto 300). Si Redis no está
configurado o falla, degrada en silencio: nunca rompe la escritura del perfil
(regla de arquitectura #4)."""

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from app.application.calculo_score import VERSION_REGLAS
from app.application.identificadores import id_opaco


class PuertoAlmacenClaveValor(Protocol):
    def get(self, nombre: str) -> bytes | str | None: ...

    def set(self, nombre: str, valor: str, ex: int | None = None) -> object: ...


@dataclass(frozen=True)
class ScoreCacheado:
    score_total: int
    nivel_riesgo: str
    fecha_vigencia: str
    version_reglas: str
    desglose_score: list[dict[str, Any]]
    tipo: str
    fuentes: list[dict[str, Any]]
    perfil_version: int | None = None

    def como_dict(self) -> dict[str, Any]:
        datos = asdict(self)
        if datos["perfil_version"] is None:
            del datos["perfil_version"]
        return datos


def clave_score(cliente_id: str, version_reglas: str = VERSION_REGLAS) -> str:
    return f"score:{id_opaco(cliente_id)}:{version_reglas}"


def clave_oferta(cliente_id: str, perfil_version: int, version_reglas: str = VERSION_REGLAS) -> str:
    return f"oferta:{id_opaco(cliente_id)}:{perfil_version}:{version_reglas}"


class CacheScore:
    def __init__(
        self,
        almacen: PuertoAlmacenClaveValor | None = None,
        ttl_segundos: int = 300,
    ) -> None:
        self._almacen = almacen
        self._ttl_segundos = ttl_segundos

    def obtener(self, clave: str) -> dict[str, Any] | None:
        if self._almacen is None:
            return None
        try:
            crudo = self._almacen.get(clave)
        except Exception:
            return None
        if crudo is None:
            return None
        return json.loads(crudo)

    def guardar(self, clave: str, valor: dict[str, Any] | ScoreCacheado) -> None:
        if self._almacen is None:
            return
        payload = valor.como_dict() if isinstance(valor, ScoreCacheado) else valor
        try:
            self._almacen.set(clave, json.dumps(payload), ex=self._ttl_segundos)
        except Exception:
            pass

    def guardar_score_cliente(
        self,
        cliente_id: str,
        perfil_version: int,
        resultado_score: dict[str, Any] | ScoreCacheado,
        version_reglas: str | None = None,
    ) -> None:
        if self._almacen is None:
            return
        payload = (
            resultado_score.como_dict()
            if isinstance(resultado_score, ScoreCacheado)
            else dict(resultado_score)
        )
        version = version_reglas or str(payload.get("version_reglas") or VERSION_REGLAS)
        payload["perfil_version"] = perfil_version
        payload["version_reglas"] = version
        self.guardar(clave_score(cliente_id, version), payload)
        self.guardar(clave_oferta(cliente_id, perfil_version, version), payload)
