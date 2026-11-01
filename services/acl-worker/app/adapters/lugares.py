"""Adaptador de lugares (T19, KAN-26): autocompletar y validar una dirección con Google Places.

Puerto y adaptador del ACL Worker (regla #2: el BFF no habla con Google). Timeout duro de 700 ms.
Sin llave (`GOOGLE_PLACES_API_KEY`) responde un stub determinista, para local y CI. Si Google no
responde, devuelve `verificado = False` sin lanzar: la cotización no se bloquea (el BFF marca la
dirección «sin verificar»). La dirección viaja a Google: no se registra en logs.
"""

import logging
import os
from typing import Any, Protocol

import httpx

LOG = logging.getLogger(__name__)
TIMEOUT_SEGUNDOS = 0.7
PAISES_SOPORTADOS = ("CO", "MX", "BR", "CL", "PE", "US", "ES")
_URL = "https://maps.googleapis.com/maps/api"


class PuertoLugares(Protocol):
    def autocompletar(self, texto: str, pais: str | None) -> list[dict[str, str]]: ...

    def validar(self, place_id: str) -> dict[str, Any]: ...


def _sin_verificar() -> dict[str, Any]:
    return {"verificado": False, "direccion": None, "ciudad": None, "pais": None}


class AdaptadorLugaresStub:
    """Sin llave: sugiere la dirección escrita; valida un `place_id` `stub:<país>:<texto>`."""

    def autocompletar(self, texto: str, pais: str | None) -> list[dict[str, str]]:
        pais = (pais or "CO").upper()
        if pais not in PAISES_SOPORTADOS or len(texto.strip()) < 3:
            return []
        return [{"place_id": f"stub:{pais}:{texto.strip()}", "descripcion": texto.strip()}]

    def validar(self, place_id: str) -> dict[str, Any]:
        partes = place_id.split(":", 2)
        if len(partes) != 3 or partes[0] != "stub" or partes[1] not in PAISES_SOPORTADOS:
            return _sin_verificar()
        return {"verificado": True, "direccion": partes[2], "ciudad": None, "pais": partes[1]}


class AdaptadorLugaresGoogle:
    def __init__(self, llave: str, cliente: httpx.Client | None = None) -> None:
        self._llave = llave
        self._cliente = cliente or httpx.Client(timeout=TIMEOUT_SEGUNDOS)

    def autocompletar(self, texto: str, pais: str | None) -> list[dict[str, str]]:
        params = {"input": texto, "key": self._llave, "types": "address"}
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


def lugares_desde_entorno() -> PuertoLugares:
    llave = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    return AdaptadorLugaresGoogle(llave) if llave else AdaptadorLugaresStub()
