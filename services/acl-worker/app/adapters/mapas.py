"""Adaptador de mapas (HU-M06, contrato v0.51, B8): tiempos y rutas de los prestadores hacia el
cliente, para pintar en la app las rutas tipo semáforo.

- `mapbox`: **Matrix API** (`driving-traffic`) para los tiempos de todos los destinos y **Directions
  API** (`polyline6`) para la geometría de los que pida quien llama (los 3 destacados). Token
  **secreto** `MAPBOX_TOKEN` (Secret Manager `mapbox-token`): nunca en la app.
- `simulado` (local y CI): línea recta y tiempos con las reglas de la app (Haversine a 20 km/h + 2
  min). Va dentro del ACL y no en un `stub-mapas` aparte (ajuste A24 del plan): mismo resultado,
  un contenedor menos.

La ubicación del cliente viaja a Mapbox (lo decidió Felipe el 2026-11-10: por el ACL, no desde el
teléfono). No se registra en logs. Sin Circuit Breaker aquí: lo pone el endpoint.
"""

import hashlib
import json
import logging
import math
import os
from typing import Any, Protocol

import httpx

LOG = logging.getLogger(__name__)
TIMEOUT_SEGUNDOS = 0.7
_URL = "https://api.mapbox.com"
VELOCIDAD_KMH = 20
ALISTAMIENTO_MIN = 2
RADIO_TIERRA_KM = 6371
MARCADOR_SECRETO = "pendiente-de-cargar"


class PuertoMapas(Protocol):
    proveedor: str

    def rutas(
        self, origen: tuple[float, float], destinos: list[dict], con_geometria: set[str]
    ) -> list[dict]:
        """`destinos`: `[{id, latitud, longitud}]`. Devuelve `[{prestador_id, distancia_metros,
        duracion_segundos, geometria, estimado}]`. Lanza si el proveedor no responde."""
        ...


def codificar_polyline6(puntos: list[tuple[float, float]]) -> str:
    """Algoritmo de polilínea de Google con precisión 1e6 (la de Mapbox `polyline6`).
    `puntos` en (latitud, longitud)."""
    resultado: list[str] = []
    previo = (0, 0)
    for lat, lon in puntos:
        actual = (round(lat * 1e6), round(lon * 1e6))
        for valor in (actual[0] - previo[0], actual[1] - previo[1]):
            v = ~(valor << 1) if valor < 0 else valor << 1
            while v >= 0x20:
                resultado.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            resultado.append(chr(v + 63))
        previo = actual
    return "".join(resultado)


def distancia_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    d_lat = math.radians(b[0] - a[0])
    d_lon = math.radians(b[1] - a[1])
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(d_lon / 2) ** 2
    )
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(h))


class AdaptadorMapasSimulado:
    proveedor = "simulado"

    def rutas(
        self, origen: tuple[float, float], destinos: list[dict], con_geometria: set[str]
    ) -> list[dict]:
        rutas = []
        for d in destinos:
            punto = (d["latitud"], d["longitud"])
            km = distancia_km(punto, origen)
            minutos = math.ceil(km / VELOCIDAD_KMH * 60) + ALISTAMIENTO_MIN
            rutas.append(
                {
                    "prestador_id": d["id"],
                    "distancia_metros": round(km * 1000),
                    "duracion_segundos": minutos * 60,
                    # Del prestador hacia el cliente: así llega a la puerta.
                    "geometria": (
                        codificar_polyline6([punto, origen]) if d["id"] in con_geometria else None
                    ),
                    "estimado": True,
                }
            )
        return rutas


class AdaptadorMapasMapbox:
    proveedor = "mapbox"

    def __init__(self, token: str, cliente: httpx.Client | None = None) -> None:
        self._token = token
        self._cliente = cliente or httpx.Client(timeout=TIMEOUT_SEGUNDOS)

    def rutas(
        self, origen: tuple[float, float], destinos: list[dict], con_geometria: set[str]
    ) -> list[dict]:
        if not destinos:
            return []
        # Matrix: el cliente es el índice 0 (destino) y los prestadores, las fuentes.
        coordenadas = ";".join(
            f"{lon},{lat}"
            for lat, lon in [origen, *((d["latitud"], d["longitud"]) for d in destinos)]
        )
        fuentes = ";".join(str(i) for i in range(1, len(destinos) + 1))
        r = self._cliente.get(
            f"{_URL}/directions-matrix/v1/mapbox/driving-traffic/{coordenadas}",
            params={
                "sources": fuentes,
                "destinations": "0",
                "annotations": "duration,distance",
                "access_token": self._token,
            },
        )
        r.raise_for_status()
        matriz = r.json()
        rutas = []
        for i, d in enumerate(destinos):
            duracion = matriz["durations"][i][0]
            distancia = matriz["distances"][i][0]
            if duracion is None or distancia is None:
                continue  # Mapbox no encontró camino: la app usa la línea recta.
            geometria = None
            if d["id"] in con_geometria:
                geometria = self._geometria((d["latitud"], d["longitud"]), origen)
            rutas.append(
                {
                    "prestador_id": d["id"],
                    "distancia_metros": round(distancia),
                    "duracion_segundos": round(duracion),
                    "geometria": geometria,
                    "estimado": False,
                }
            )
        return rutas

    def _geometria(self, desde: tuple[float, float], hasta: tuple[float, float]) -> str | None:
        r = self._cliente.get(
            f"{_URL}/directions/v5/mapbox/driving-traffic/"
            f"{desde[1]},{desde[0]};{hasta[1]},{hasta[0]}",
            params={
                "geometries": "polyline6",
                "overview": "full",
                "access_token": self._token,
            },
        )
        r.raise_for_status()
        rutas = r.json().get("routes") or []
        return rutas[0]["geometry"] if rutas else None


class AdaptadorMapasSinToken:
    """`MAPAS_PROVEEDOR=mapbox` sin el token real (el secreto aún tiene el valor marcador):
    falla siempre, el circuito se abre y la app sigue con tiempos aproximados."""

    proveedor = "mapbox"

    def rutas(self, origen, destinos, con_geometria) -> list[dict]:
        raise RuntimeError("MAPBOX_TOKEN sin cargar")


def mapas_desde_entorno() -> PuertoMapas:
    token = os.environ.get("MAPBOX_TOKEN", "").strip()
    if token == MARCADOR_SECRETO:
        token = ""
    if os.environ.get("MAPAS_PROVEEDOR", "").strip().lower() == "mapbox":
        if not token:
            LOG.warning("MAPAS_PROVEEDOR=mapbox sin token: rutas no disponibles")
            return AdaptadorMapasSinToken()
        return AdaptadorMapasMapbox(token)
    return AdaptadorMapasSimulado()


class CacheMapas:
    """Caché de rutas en Redis por celda de ~200 m y 2 min (B8): abrir el mapa dos veces desde
    el mismo lugar no paga dos veces a Mapbox. La llave es un hash (nunca las coordenadas)."""

    CELDA_GRADOS = 0.0018  # ~200 m en latitud
    PREFIJO = "mapas:rutas"

    def __init__(self, almacen: Any, ttl_segundos: int = 120) -> None:
        self._almacen = almacen
        self._ttl = ttl_segundos

    def _clave(self, origen: tuple[float, float], ids: list[str], geometria: set[str]) -> str:
        celda = (round(origen[0] / self.CELDA_GRADOS), round(origen[1] / self.CELDA_GRADOS))
        valor = json.dumps([celda, sorted(ids), sorted(geometria)])
        return f"{self.PREFIJO}:{hashlib.sha256(valor.encode()).hexdigest()}"

    def obtener(self, origen, ids, geometria) -> list[dict] | None:
        try:
            valor = self._almacen.get(self._clave(origen, ids, geometria))
        except Exception:
            return None
        return json.loads(valor) if valor else None

    def guardar(self, origen, ids, geometria, rutas: list[dict]) -> None:
        try:
            self._almacen.set(self._clave(origen, ids, geometria), json.dumps(rutas), ex=self._ttl)
        except Exception:
            LOG.warning("Redis no guardó las rutas en caché")
