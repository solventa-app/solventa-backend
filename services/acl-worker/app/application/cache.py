"""Caché cache-aside del ACL Worker (HA-LAT-002, CA-W01-04): último valor
bueno por fuente y cliente, usado SOLO para degradar cuando el proveedor
real falla o el circuito está abierto.

TTL por defecto de 5 minutos (`CACHE_TTL_SEGUNDOS`): además de acotar qué tan
viejo puede ser un dato degradado, hace que la caché expire sola dentro de la
ventana de CA-W01-08 (revocación de consentimiento en ≤ 5 min). La
invalidación ACTIVA al revocar consentimiento (p. ej. consumiendo
`consentimiento.revocado`) queda fuera de alcance de esta tarea — ver README.

La clave nunca contiene el `cliente_id` en crudo (se guarda su hash): defensa
en profundidad, aunque esto vive en Redis interno, no en logs de aplicación.
"""

import hashlib
import json
from datetime import datetime
from typing import Protocol

from app.domain.puertos import DatosFuente


class PuertoAlmacenClaveValor(Protocol):
    """Lo mínimo que el cache-aside necesita de un cliente Redis — permite
    inyectar un fake en pruebas sin levantar Redis de verdad."""

    def get(self, nombre: str) -> bytes | str | None: ...

    def set(self, nombre: str, valor: str, ex: int | None = None) -> object: ...


class CacheFuentes:
    def __init__(self, almacen: PuertoAlmacenClaveValor, ttl_segundos: int) -> None:
        self._almacen = almacen
        self._ttl_segundos = ttl_segundos

    @staticmethod
    def _clave(fuente: str, cliente_id: str) -> str:
        hash_cliente = hashlib.sha256(cliente_id.encode("utf-8")).hexdigest()
        return f"acl:fuente:{fuente}:{hash_cliente}"

    def guardar(self, fuente: str, cliente_id: str, datos: DatosFuente) -> None:
        valor = json.dumps({"datos": datos.datos, "capturado_en": datos.capturado_en.isoformat()})
        try:
            self._almacen.set(self._clave(fuente, cliente_id), valor, ex=self._ttl_segundos)
        except Exception:
            # Cache-aside: un fallo de Redis nunca debe tumbar la respuesta ya
            # resuelta al llamador (regla de arquitectura #4).
            pass

    def obtener(self, fuente: str, cliente_id: str) -> DatosFuente | None:
        try:
            crudo = self._almacen.get(self._clave(fuente, cliente_id))
        except Exception:
            return None
        if crudo is None:
            return None
        cuerpo = json.loads(crudo)
        return DatosFuente(
            fuente=fuente,
            datos=cuerpo["datos"],
            capturado_en=datetime.fromisoformat(cuerpo["capturado_en"]),
        )
