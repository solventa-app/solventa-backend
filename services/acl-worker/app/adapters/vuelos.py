"""Adaptador de vuelos (HU-M04, KAN-259, Fase 3): traducción pura a HTTP contra AviationStack
(`https://api.aviationstack.com/v1/flights`, con llave) o, en local y en el CI, contra
`stubs/eventos-parametricos`, que imita la misma forma de respuesta en `GET /v1/flights`.

Mismo rol que `AdaptadorVehiculos`: sin Circuit Breaker ni caché aquí; eso lo orquesta
`ServicioEventosParametricos`. Un vuelo+fecha que el proveedor no tiene es una respuesta válida,
no una falla: vuelve como `MedicionVuelo(encontrado=False)` y no abre el circuito.
"""

from datetime import UTC, datetime

import httpx

from app.domain.puertos import ConsultaVuelo, MedicionVuelo

CANCELADO = "cancelled"

# Vuelo+fecha sintéticos con los que la sonda de recuperación prueba al proveedor (regla 5):
# nunca el vuelo de una póliza real. No existen de verdad, así que la respuesta esperada es
# "no encontrado" (el host respondió, que es lo único que importa a la sonda).
VUELO_SONDA = ConsultaVuelo(numero_vuelo="XX0000", fecha="2000-01-01")


class AdaptadorVuelo:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._api_key = api_key
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def consultar(self, consulta: ConsultaVuelo, timeout_s: float | None = None) -> MedicionVuelo:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.get(
            "/v1/flights",
            params={
                "access_key": self._api_key,
                "flight_iata": consulta.numero_vuelo,
                "flight_date": consulta.fecha,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        datos = respuesta.json().get("data") or []
        if not datos:
            return MedicionVuelo(
                encontrado=False, medido_en=datetime.now(UTC).isoformat(), fuente="aviationstack"
            )
        vuelo = datos[0]
        retraso = (vuelo.get("departure") or {}).get("delay") or 0
        return MedicionVuelo(
            encontrado=True,
            retraso_minutos=int(retraso),
            cancelado=vuelo.get("flight_status") == CANCELADO,
            medido_en=datetime.now(UTC).isoformat(),
            fuente="aviationstack",
        )

    def sondear(self) -> None:
        """Llamada sintética solo para la sonda de recuperación del circuito: el vuelo sintético
        no existe, así que la respuesta esperada es `encontrado=False`, no una falla."""
        self.consultar(VUELO_SONDA)

    def cerrar(self) -> None:
        self._cliente.close()
