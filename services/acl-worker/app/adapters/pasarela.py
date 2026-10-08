"""Adaptador de la pasarela de pagos: traducción pura a HTTP contra
`stubs/pasarela` (T-W05-2, lado ACL). Nunca recibe ni reenvía datos de
tarjeta: solo el `token_medio_pago` ya tokenizado por el proveedor PCI-DSS
(regla de arquitectura #6).
"""

import httpx

from app.domain.puertos import OrdenCobro, ResultadoCobro


class AdaptadorPasarela:
    """Implementa `PuertoPasarelaPagos`."""

    def __init__(
        self, base_url: str, timeout_s: float, cliente: httpx.Client | None = None
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"))

    def cobrar(self, orden: OrdenCobro, timeout_s: float | None = None) -> ResultadoCobro:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/cobros",
            json={
                "clave_idempotencia": orden.clave_idempotencia,
                "poliza_id": orden.poliza_id,
                "monto": orden.monto,
                "moneda": orden.moneda,
                "token_medio_pago": orden.token_medio_pago,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return ResultadoCobro(
            estado=cuerpo["estado"],
            referencia=cuerpo.get("referencia"),
            motivo=cuerpo.get("motivo"),
            detalle=cuerpo.get("detalle", {}),
        )

    def sondear(self) -> None:
        """Llama `GET /v1/estado` (obedece el modo simulado) en vez de ejecutar un
        cobro real: sondear la recuperación del circuito nunca debe crear una
        orden de pago de verdad. Ver `app/application/sonda.py`."""
        respuesta = self._cliente.get("/v1/estado", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
