"""Adaptador de lugares (T19, KAN-26): autocompletar y validar una dirección con Google Places.

HU-M06 (contrato v0.50, B9): `ubicar` convierte la dirección que el cliente escribió en coordenadas
para el mapa de asistencia: con `place_id`, Place Details (campo `geometry`); sin él, la Geocoding
API con el texto, restringida a Colombia. Recibe el idioma de la app (`es`/`en`) para las
sugerencias y la dirección normalizada. Responde códigos, nunca textos para el usuario.

Puerto y adaptador del ACL Worker (regla #2: el BFF no habla con Google). Timeout duro de 700 ms.
Sin llave (`GOOGLE_PLACES_API_KEY`) responde un stub determinista, para local y CI. Si Google no
responde, devuelve `verificado = False` sin lanzar: la cotización no se bloquea (el BFF marca la
dirección «sin verificar»). La dirección viaja a Google: no se registra en logs.
"""

import hashlib
import json
import logging
import os
from typing import Any, Protocol

import httpx

LOG = logging.getLogger(__name__)
TIMEOUT_SEGUNDOS = 0.7
PAISES_SOPORTADOS = ("CO", "MX", "BR", "CL", "PE", "US", "ES")
_URL = "https://maps.googleapis.com/maps/api"


IDIOMAS = ("es", "en")
NO_ENCONTRADA = "DIRECCION_NO_ENCONTRADA"
SERVICIO_NO_DISPONIBLE = "SERVICIO_NO_DISPONIBLE"


class PuertoLugares(Protocol):
    def autocompletar(
        self, texto: str, pais: str | None, idioma: str | None = None
    ) -> list[dict[str, str]]: ...

    def validar(self, place_id: str) -> dict[str, Any]: ...

    def ubicar(self, place_id: str | None, texto: str | None, idioma: str) -> dict[str, Any]:
        """Lanza si el proveedor no responde (lo cuenta el Circuit Breaker del endpoint)."""
        ...

    def inversa(self, latitud: float, longitud: float) -> str | None:
        """HU-M06 (v0.51, B8): dirección aproximada en español de un punto, para la auditoría de
        rutas. `None` si no hay. Lanza si el proveedor no responde."""
        ...


def _idioma(idioma: str | None) -> str:
    return idioma if idioma in IDIOMAS else "es"


def no_encontrada(codigo: str = NO_ENCONTRADA) -> dict[str, Any]:
    return {
        "encontrada": False,
        "latitud": None,
        "longitud": None,
        "direccion": None,
        "codigo": codigo,
    }


def encontrada(latitud: float, longitud: float, direccion: str) -> dict[str, Any]:
    return {
        "encontrada": True,
        "latitud": latitud,
        "longitud": longitud,
        "direccion": direccion,
        "codigo": None,
    }


def _sin_verificar() -> dict[str, Any]:
    return {"verificado": False, "direccion": None, "ciudad": None, "pais": None}


class AdaptadorLugaresStub:
    """Sin llave: sugiere la dirección escrita; valida un `place_id` `stub:<país>:<texto>`."""

    # Un punto fijo en Bogotá (Parque de la 93, como el GPS de `mock`) y un corrimiento
    # determinista por texto, para que direcciones distintas caigan en lugares distintos.
    CENTRO = (4.6767, -74.0483)

    def autocompletar(
        self, texto: str, pais: str | None, idioma: str | None = None
    ) -> list[dict[str, str]]:
        pais = (pais or "CO").upper()
        if pais not in PAISES_SOPORTADOS or len(texto.strip()) < 3:
            return []
        return [{"place_id": f"stub:{pais}:{texto.strip()}", "descripcion": texto.strip()}]

    def ubicar(self, place_id: str | None, texto: str | None, idioma: str) -> dict[str, Any]:
        """`stub:CO:<texto>` o el texto: lo ubica cerca del centro de Bogotá. Un texto con
        «noexiste» no se encuentra (para probar el mensaje en la app)."""
        if place_id:
            partes = place_id.split(":", 2)
            if len(partes) != 3 or partes[0] != "stub" or partes[1] != "CO":
                return no_encontrada()
            texto = partes[2]
        texto = (texto or "").strip()
        if len(texto) < 3 or "noexiste" in texto.lower():
            return no_encontrada()
        semilla = int(hashlib.sha256(texto.encode("utf-8")).hexdigest()[:8], 16)
        lat = self.CENTRO[0] + ((semilla % 200) - 100) / 20000
        lon = self.CENTRO[1] + (((semilla // 200) % 200) - 100) / 20000
        return encontrada(round(lat, 6), round(lon, 6), texto)

    def inversa(self, latitud: float, longitud: float) -> str | None:
        return f"Cerca de {latitud:.3f}, {longitud:.3f} (simulada)"

    def validar(self, place_id: str) -> dict[str, Any]:
        partes = place_id.split(":", 2)
        if len(partes) != 3 or partes[0] != "stub" or partes[1] not in PAISES_SOPORTADOS:
            return _sin_verificar()
        return {"verificado": True, "direccion": partes[2], "ciudad": None, "pais": partes[1]}


class AdaptadorLugaresGoogle:
    def __init__(self, llave: str, cliente: httpx.Client | None = None) -> None:
        self._llave = llave
        self._cliente = cliente or httpx.Client(timeout=TIMEOUT_SEGUNDOS)

    def autocompletar(
        self, texto: str, pais: str | None, idioma: str | None = None
    ) -> list[dict[str, str]]:
        params = {
            "input": texto,
            "key": self._llave,
            "types": "address",
            "language": _idioma(idioma),
        }
        if pais and pais.upper() in PAISES_SOPORTADOS:
            params["components"] = f"country:{pais.lower()}"
        else:
            params["components"] = "|".join(f"country:{p.lower()}" for p in PAISES_SOPORTADOS)
        try:
            r = self._cliente.get(f"{_URL}/place/autocomplete/json", params=params)
            r.raise_for_status()
            return [
                {"place_id": p["place_id"], "descripcion": p["description"]}
                for p in r.json().get("predictions", [])
            ]
        except (httpx.HTTPError, ValueError, KeyError):
            LOG.warning("Places no respondió al autocompletar")
            return []

    def validar(self, place_id: str) -> dict[str, Any]:
        params = {
            "place_id": place_id,
            "key": self._llave,
            "fields": "formatted_address,address_components",
        }
        try:
            r = self._cliente.get(f"{_URL}/place/details/json", params=params)
            r.raise_for_status()
            res = r.json().get("result")
            if not res:
                return _sin_verificar()
            comps = res.get("address_components", [])

            def buscar(tipo: str, corto: bool = False) -> str | None:
                for c in comps:
                    if tipo in c.get("types", []):
                        return c["short_name" if corto else "long_name"]
                return None

            return {
                "verificado": True,
                "direccion": res.get("formatted_address"),
                "ciudad": buscar("locality") or buscar("administrative_area_level_2"),
                "pais": buscar("country", corto=True),
            }
        except (httpx.HTTPError, ValueError, KeyError):
            LOG.warning("Places no respondió al validar")
            return _sin_verificar()

    def ubicar(self, place_id: str | None, texto: str | None, idioma: str) -> dict[str, Any]:
        """Lanza `httpx.HTTPError` si Google no responde (lo cuenta el circuito)."""
        if place_id:
            r = self._cliente.get(
                f"{_URL}/place/details/json",
                params={
                    "place_id": place_id,
                    "key": self._llave,
                    "fields": "formatted_address,geometry",
                    "language": _idioma(idioma),
                },
            )
            r.raise_for_status()
            res = r.json().get("result")
        else:
            r = self._cliente.get(
                f"{_URL}/geocode/json",
                params={
                    "address": texto or "",
                    "key": self._llave,
                    "components": "country:CO",
                    "language": _idioma(idioma),
                },
            )
            r.raise_for_status()
            resultados = r.json().get("results") or []
            res = resultados[0] if resultados else None
        try:
            punto = res["geometry"]["location"] if res else None
            if not punto:
                return no_encontrada()
            return encontrada(float(punto["lat"]), float(punto["lng"]), res["formatted_address"])
        except (KeyError, TypeError, ValueError):
            return no_encontrada()

    def inversa(self, latitud: float, longitud: float) -> str | None:
        """Geocoding inverso, siempre en español (soporte y auditoría la leen igual)."""
        r = self._cliente.get(
            f"{_URL}/geocode/json",
            params={
                "latlng": f"{latitud},{longitud}",
                "key": self._llave,
                "language": "es",
                "result_type": "street_address|route|neighborhood",
            },
        )
        r.raise_for_status()
        resultados = r.json().get("results") or []
        return resultados[0].get("formatted_address") if resultados else None


class AdaptadorLugaresSinLlave:
    """`PLACES_PROVEEDOR=google` sin una llave real (el secreto aún tiene su valor marcador): nunca
    inventa un lugar. Sin sugerencias y todo «sin verificar», como si Google no respondiera."""

    def autocompletar(
        self, texto: str, pais: str | None, idioma: str | None = None
    ) -> list[dict[str, str]]:
        return []

    def validar(self, place_id: str) -> dict[str, Any]:
        return _sin_verificar()

    def ubicar(self, place_id: str | None, texto: str | None, idioma: str) -> dict[str, Any]:
        return no_encontrada(SERVICIO_NO_DISPONIBLE)

    def inversa(self, latitud: float, longitud: float) -> str | None:
        return None


# Valor que Terraform deja en un secreto hasta que se carga el real (infra/servicios.tf).
MARCADOR_SECRETO = "pendiente-de-cargar"


def lugares_desde_entorno() -> PuertoLugares:
    """`PLACES_PROVEEDOR`: `google` en la nube (nunca el stub: aceptaría direcciones inventadas) y
    `simulado` o vacío en local y CI (Google si hay llave, si no el stub)."""
    llave = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if llave == MARCADOR_SECRETO:
        llave = ""
    if os.environ.get("PLACES_PROVEEDOR", "").strip().lower() == "google":
        if not llave:
            LOG.warning("PLACES_PROVEEDOR=google sin llave: direcciones sin verificar")
            return AdaptadorLugaresSinLlave()
        return AdaptadorLugaresGoogle(llave)
    return AdaptadorLugaresGoogle(llave) if llave else AdaptadorLugaresStub()


class CacheLugares:
    """Caché de `ubicar` en Redis (B9): la llave lleva el idioma y un hash del lugar (nunca el
    texto, que es una dirección). Solo guarda los encontrados. Si Redis falla, sigue sin caché."""

    PREFIJO = "lugares:ubicar"

    def __init__(self, almacen: Any, ttl_segundos: int = 24 * 3600) -> None:
        self._almacen = almacen
        self._ttl = ttl_segundos

    def _clave(self, place_id: str | None, texto: str | None, idioma: str) -> str:
        valor = f"p:{place_id}" if place_id else f"t:{(texto or '').strip().lower()}"
        return f"{self.PREFIJO}:{idioma}:{hashlib.sha256(valor.encode('utf-8')).hexdigest()}"

    def obtener(self, place_id: str | None, texto: str | None, idioma: str) -> dict | None:
        try:
            valor = self._almacen.get(self._clave(place_id, texto, idioma))
        except Exception:
            return None
        return json.loads(valor) if valor else None

    def _clave_inversa(self, latitud: float, longitud: float) -> str:
        # Celda de ~100 m (B8): no se paga una llamada por cada apertura del mapa.
        celda = f"{round(latitud / 0.0009)}:{round(longitud / 0.0009)}"
        return f"lugares:inversa:{hashlib.sha256(celda.encode()).hexdigest()}"

    def obtener_inversa(self, latitud: float, longitud: float) -> str | None:
        try:
            valor = self._almacen.get(self._clave_inversa(latitud, longitud))
        except Exception:
            return None
        return valor.decode() if isinstance(valor, bytes) else valor

    def guardar_inversa(self, latitud: float, longitud: float, direccion: str) -> None:
        try:
            self._almacen.set(self._clave_inversa(latitud, longitud), direccion, ex=self._ttl)
        except Exception:
            LOG.warning("Redis no guardó la dirección aproximada en caché")

    def guardar(self, place_id: str | None, texto: str | None, idioma: str, r: dict) -> None:
        if not r.get("encontrada"):
            return
        try:
            self._almacen.set(self._clave(place_id, texto, idioma), json.dumps(r), ex=self._ttl)
        except Exception:
            LOG.warning("Redis no guardó la ubicación en caché")
