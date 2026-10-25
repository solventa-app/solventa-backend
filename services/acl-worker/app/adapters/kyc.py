"""Adaptador de KYC: traducción pura a HTTP contra `stubs/kyc` (HU-W03, W03-10). Implementa
`PuertoKyc`.

Solo envía el identificador opaco del cliente: nunca un documento ni un dato personal (regla de
arquitectura #6). El consolidador de KYC real (Truora) queda para el Sprint 3."""

import httpx

from app.domain.puertos import ResultadoKyc, SolicitudKyc


class AdaptadorKyc:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def verificar(self, solicitud: SolicitudKyc, timeout_s: float | None = None) -> ResultadoKyc:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/verificaciones",
            json={
                "referencia_comercio": solicitud.id_verificacion,
                "cliente_id": solicitud.cliente_id,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return ResultadoKyc(
            estado=cuerpo["estado"],
            referencia=cuerpo.get("referencia"),
            motivo=cuerpo.get("motivo"),
            verificada_en=cuerpo.get("verificada_en"),
        )

    def sondear(self) -> None:
        """`GET /v1/estado` (obedece el modo simulado) en vez de verificar: sondear la recuperación
        del circuito nunca debe crear una verificación de verdad. Ver `app/application/sonda.py`."""
        respuesta = self._cliente.get("/v1/estado", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
