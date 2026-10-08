"""Cache-Aside del SCORE calculado por RATING (HA-LAT-001).

Mismo patrón que `services/acl-worker/app/application/cache.py` (duplicado a
propósito — regla del repo: sin código compartido entre servicios): un
`Protocol` mínimo (`get`/`set`) para poder inyectar un cliente Redis real o un
doble de prueba, y un fallo de Redis que **nunca** se propaga (cache-aside:
el peor caso es recalcular, no romper la respuesta — regla de arquitectura
#4).

Solo se cachea el **score** (y los metadatos de fuentes/tipo de oferta que de
él se derivan): la prima NUNCA se cachea aquí porque depende de `cobertura`,
un dato de la solicitud que puede cambiar en cada llamada y que la clave
especificada (`oferta:{cliente_id}:{perfil_version}:{version_reglas}`) no
incluye. Ver la nota de diseño en `servicio_oferta.py`."""

import json
from typing import Any, Protocol


class PuertoAlmacenClaveValor(Protocol):
    """Lo mínimo que el cache-aside necesita de un cliente Redis — permite
    inyectar un fake en pruebas sin levantar Redis de verdad."""

    def get(self, nombre: str) -> bytes | str | None: ...

    def set(self, nombre: str, valor: str, ex: int | None = None) -> object: ...


class CacheScore:
    def __init__(self, almacen: PuertoAlmacenClaveValor, ttl_segundos: int) -> None:
        self._almacen = almacen
        self._ttl_segundos = ttl_segundos

    def obtener(self, clave: str) -> dict[str, Any] | None:
        try:
            crudo = self._almacen.get(clave)
        except Exception:
            return None
        if crudo is None:
            return None
        return json.loads(crudo)

    def guardar(self, clave: str, valor: dict[str, Any]) -> None:
        try:
            self._almacen.set(clave, json.dumps(valor), ex=self._ttl_segundos)
        except Exception:
            # Cache-aside: un fallo de Redis nunca debe tumbar la respuesta ya
            # resuelta al llamador (regla de arquitectura #4).
            pass
