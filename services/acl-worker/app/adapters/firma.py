"""Adaptador de la firma electrónica: traducción pura a HTTP contra `stubs/firma` (HU-W03,
CA-W03-03). Implementa `PuertoFirmaElectronica`.

Solo envía el hash del documento, la versión y el identificador opaco del firmante: nunca el
contenido del documento ni un dato personal (regla de arquitectura #6)."""

import httpx

from app.domain.puertos import ResultadoFirma, SolicitudFirma


class AdaptadorFirma:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def firmar(self, solicitud: SolicitudFirma, timeout_s: float | None = None) -> ResultadoFirma:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/firmas",
            json={
                "referencia_comercio": solicitud.id_firma,
                "documento_hash": solicitud.documento_hash,
                "version_documento": solicitud.version_documento,
                "firmante_id": solicitud.firmante_id,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return ResultadoFirma(
            estado=cuerpo["estado"],
            referencia=cuerpo.get("referencia"),
            firmada_en=cuerpo.get("firmada_en"),
            motivo=cuerpo.get("motivo"),
        )

    def sondear(self) -> None:
        """`GET /v1/estado` (obedece el modo simulado) en vez de firmar: sondear la recuperación del
        circuito nunca debe crear una firma de verdad. Ver `app/application/sonda.py`."""
        respuesta = self._cliente.get("/v1/estado", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
