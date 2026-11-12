"""Adaptador de la red de prestadores de asistencia en sitio (HU-M06, contrato v0.46, B7).

Traducción pura a HTTP contra `stubs/prestadores` mientras no haya prestadores reales. Sin Circuit
Breaker aquí: lo orquesta `ServicioPrestadores`. La ubicación del cliente viaja al prestador (es
adónde debe llegar) y nunca a los logs.
"""

import httpx

from app.domain.puertos import DesenlaceAsistencia, PedidoAsistencia


class AdaptadorPrestadores:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def solicitar(
        self, pedido: PedidoAsistencia, timeout_s: float | None = None
    ) -> DesenlaceAsistencia:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/asistencias",
            json={
                "asistencia_id": pedido.asistencia_id,
                "intento": pedido.intento,
                "tipo_asistencia": pedido.tipo_asistencia,
                "prestador_id": pedido.prestador_id,
                "prestador_latitud": pedido.prestador_latitud,
                "prestador_longitud": pedido.prestador_longitud,
                "latitud": pedido.latitud,
                "longitud": pedido.longitud,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return DesenlaceAsistencia(
            estado=cuerpo["estado"],
            minutos_estimados=cuerpo.get("minutos_estimados"),
            motivo=cuerpo.get("motivo"),
        )

    def sondear(self) -> None:
        """`GET /v1/estado`: sondear la recuperación del circuito nunca confirma una asistencia."""
        respuesta = self._cliente.get("/v1/estado", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
