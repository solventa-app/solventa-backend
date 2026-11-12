"""Adaptador de los datos del vehículo (póliza de auto, KAN-290): traducción pura a HTTP contra
`stubs/vehiculos` de las tres fuentes de `contracts/fuentes-vehiculo.json` (`runt-vehiculo`,
`fasecolda-valor` y `siniestralidad-placa`).

Mismo rol que `AdaptadorOpenData`: sin Circuit Breaker ni caché aquí; eso lo orquesta
`ServicioConsultaFuentes`. Dos diferencias:
- La consulta va por **placa** (`ConsultaFuente.placa`), no por cliente. La placa solo viaja al
  proveedor: no vuelve en los datos, así que no termina en la caché ni en los logs (regla 6).
- Una placa que el proveedor **no tiene** (404) es una respuesta válida, no una falla: vuelve como
  `{"encontrada": false}` y no abre el circuito ni degrada la oferta a preliminar.
"""

from datetime import UTC, datetime

import httpx

from app.application.fuentes_config import fuentes_vehiculo
from app.domain.puertos import ConsultaFuente, DatosFuente

# Placa con la que la sonda de recuperación prueba al proveedor (regla 5): nunca la de un cliente.
PLACA_SONDA = "AAA000"


class AdaptadorVehiculos:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
        fuentes: tuple[str, ...] | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)
        self._fuentes = fuentes or fuentes_vehiculo()

    def consultar(self, consulta: ConsultaFuente, timeout_s: float | None = None) -> DatosFuente:
        if not consulta.placa:
            raise ValueError("la consulta de un vehículo necesita la placa")
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            f"/v1/fuentes/{consulta.fuente}/consulta",
            json={"placa": consulta.placa},
            timeout=timeout,
        )
        if respuesta.status_code == 404 and respuesta.json().get("detail") == "placa_no_encontrada":
            datos: dict = {"encontrada": False}
        else:
            respuesta.raise_for_status()
            datos = {"encontrada": True, **respuesta.json().get("datos", {})}
            datos.pop("placa", None)
        return DatosFuente(fuente=consulta.fuente, datos=datos, capturado_en=datetime.now(UTC))

    def sondear(self) -> None:
        """Llamada sintética solo para la sonda de recuperación del circuito."""
        self.consultar(
            ConsultaFuente(
                cliente_id="sonda",
                consentimiento_id="sonda",
                fuente=self._fuentes[0],
                placa=PLACA_SONDA,
            )
        )

    def cerrar(self) -> None:
        self._cliente.close()
