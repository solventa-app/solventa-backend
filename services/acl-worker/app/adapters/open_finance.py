"""Adaptador de Open Finance: traducción pura a HTTP contra `stubs/open-finance`
(o, en el futuro, contra un sandbox real) de las 2 fuentes financieras de la
pantalla W01-04 (`cuentas-bancarias`, `historial-crediticio`).

Deliberadamente sin Circuit Breaker ni caché aquí: ese es trabajo de la capa de
aplicación (`app/application/servicio_fuentes.py`), que orquesta este
adaptador. El adaptador solo sabe hablar HTTP con el proveedor (T-W01-8).
"""

from datetime import UTC, datetime

import httpx

from app.application.fuentes_config import fuentes_open_finance
from app.domain.puertos import ConsultaFuente, DatosFuente

FUENTES_SOPORTADAS = fuentes_open_finance()


class AdaptadorOpenFinance:
    """Implementa `PuertoFuenteFinanciera`."""

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
        self._fuentes = fuentes or fuentes_open_finance() or FUENTES_SOPORTADAS

    def consultar(self, consulta: ConsultaFuente, timeout_s: float | None = None) -> DatosFuente:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            f"/v1/fuentes/{consulta.fuente}/consulta",
            json={
                "cliente_id": consulta.cliente_id,
                "consentimiento_id": consulta.consentimiento_id,
                "finalidad": consulta.finalidad,
            },
            params={
                "consentimiento_id": consulta.consentimiento_id,
                "finalidad": consulta.finalidad,
            },
            headers={
                "X-Consentimiento-Id": consulta.consentimiento_id,
                "X-Finalidad": consulta.finalidad,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return DatosFuente(
            fuente=consulta.fuente,
            datos=cuerpo.get("datos", {}),
            capturado_en=datetime.now(UTC),
        )

    def sondear(self) -> None:
        """Llamada sintética (sin cliente ni consentimiento reales) que usa SOLO la
        sonda de recuperación del circuito (regla de arquitectura #5): nunca una
        petición de usuario debe pagar el costo de probar si el proveedor volvió.
        """
        consulta = ConsultaFuente(
            cliente_id="sonda",
            consentimiento_id="sonda",
            fuente=self._fuentes[0],
            finalidad="sonda",
        )
        self.consultar(consulta)

    def cerrar(self) -> None:
        self._cliente.close()
