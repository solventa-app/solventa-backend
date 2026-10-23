"""Cliente HTTP hacia RATING (`GET {RATING_URL}/ofertas/{id}`).

POLICY no confía en el precio que manda el cliente: lo lee de la oferta que RATING guardó al
calcularla (decisión W03-1). RATING es de solo lectura para POLICY (regla de arquitectura #1:
RATING no escribe en el almacén de POLICY ni al revés)."""

from typing import Any, Protocol

import httpx


class OfertaNoEncontrada(Exception):
    """RATING no tiene la oferta: venció su vigencia o nunca existió."""


class RatingNoDisponible(Exception):
    """RATING no respondió (red, timeout, 5xx). El llamador responde un error con reintento."""


class PuertoRating(Protocol):
    def obtener_oferta(self, oferta_id: str) -> dict[str, Any]: ...


class ClienteRating:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def obtener_oferta(self, oferta_id: str) -> dict[str, Any]:
        try:
            respuesta = self._cliente.get(f"/ofertas/{oferta_id}")
        except httpx.HTTPError as error:
            raise RatingNoDisponible(type(error).__name__) from error
        if respuesta.status_code == 404:
            raise OfertaNoEncontrada(oferta_id)
        if respuesta.is_error:
            raise RatingNoDisponible(f"HTTP {respuesta.status_code}")
        return respuesta.json()

    def cerrar(self) -> None:
        self._cliente.close()
