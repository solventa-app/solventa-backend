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

from app.application.identificadores import id_opaco


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

    def invalidar_cliente(
        self,
        cliente_id: str,
        perfil_version: int | None = None,
        version_reglas: str = "v1",
    ) -> int:
        """Borra las entradas de caché del score de `cliente_id` al recibir el
        evento `perfil.actualizado` (Bloque 8, decisión 9). Fallos de Redis se
        absorben en silencio."""
        hash_cliente = id_opaco(cliente_id)
        prefijo_oferta = f"oferta:{hash_cliente}:"
        prefijo_score = f"score:{hash_cliente}:"
        claves_a_borrar: set[str] = {f"score:{hash_cliente}:{version_reglas}"}
        if perfil_version is not None:
            claves_a_borrar.add(f"oferta:{hash_cliente}:{perfil_version}:{version_reglas}")
            if perfil_version > 1:
                for version_anterior in range(1, perfil_version):
                    claves_a_borrar.add(
                        f"oferta:{hash_cliente}:{version_anterior}:{version_reglas}"
                    )

        try:
            if hasattr(self._almacen, "keys"):
                for patron in (f"{prefijo_oferta}*", f"{prefijo_score}*"):
                    for encontrada in self._almacen.keys(patron):
                        clave_str = (
                            encontrada.decode("utf-8")
                            if isinstance(encontrada, bytes)
                            else str(encontrada)
                        )
                        claves_a_borrar.add(clave_str)
            elif hasattr(self._almacen, "valores") and isinstance(self._almacen.valores, dict):
                for clave_existente in list(self._almacen.valores.keys()):
                    if clave_existente.startswith(prefijo_oferta) or clave_existente.startswith(
                        prefijo_score
                    ):
                        claves_a_borrar.add(clave_existente)

            eliminadas = 0
            if hasattr(self._almacen, "delete"):
                if claves_a_borrar:
                    resultado = self._almacen.delete(*claves_a_borrar)
                    eliminadas = (
                        int(resultado) if isinstance(resultado, int) else len(claves_a_borrar)
                    )
            elif hasattr(self._almacen, "valores") and isinstance(self._almacen.valores, dict):
                for clave in claves_a_borrar:
                    if clave in self._almacen.valores:
                        del self._almacen.valores[clave]
                        eliminadas += 1
            return eliminadas
        except Exception:
            return 0
