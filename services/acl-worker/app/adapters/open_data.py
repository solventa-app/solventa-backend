"""Adaptador de Open Data: traducción pura a HTTP contra `stubs/open-data` de
las 3 fuentes abiertas de la pantalla W01-04 (`afiliacion-pila`,
`camara-comercio`, `antecedentes-judiciales`).

Mismo rol que `AdaptadorOpenFinance`: sin Circuit Breaker ni caché aquí
(T-W01-9); eso lo orquesta `app/application/servicio_fuentes.py`.
"""

from datetime import UTC, datetime

import httpx

from app.domain.puertos import ConsultaFuente, DatosFuente

FUENTES_SOPORTADAS = ("afiliacion-pila", "camara-comercio", "antecedentes-judiciales")


class AdaptadorOpenData:
    """Implementa `PuertoFuenteAbierta`."""

    def __init__(
        self, base_url: str, timeout_s: float, cliente: httpx.Client | None = None
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"))

    def consultar(self, consulta: ConsultaFuente, timeout_s: float | None = None) -> DatosFuente:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            f"/v1/fuentes/{consulta.fuente}/consulta",
            json={"cliente_id": consulta.cliente_id},
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
        """Ver `AdaptadorOpenFinance.sondear`: llamada sintética solo para la sonda
        de recuperación del circuito, nunca para una petición de usuario."""
        consulta = ConsultaFuente(
            cliente_id="sonda", consentimiento_id="sonda", fuente=FUENTES_SOPORTADAS[0]
        )
        self.consultar(consulta)

    def cerrar(self) -> None:
        self._cliente.close()
