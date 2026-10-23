"""Segundo adaptador de la firma electrónica (HA-MOD-001, EC031, CA-W03-10).

Habla con un proveedor que expone OTRA API que el primero: otras rutas, otros nombres de campos
(`signature_request_id`, `document_digest`, `signer`) y otros estados (`SIGNED`, `PENDING`). Solo
traduce hacia y desde el contrato del puerto `PuertoFirmaElectronica`; el Circuit Breaker, POLICY y
el BFF no se enteran. Cambiar de proveedor es elegir este adaptador con `PROVEEDOR_FIRMA=alterna`:
ninguna línea del Core cambia ni se vuelve a desplegar."""

import httpx

from app.domain.puertos import EstadoFirma, ResultadoFirma, SolicitudFirma

ESTADOS: dict[str, EstadoFirma] = {"SIGNED": "firmada", "PENDING": "pendiente"}


class AdaptadorFirmaAlterna:
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
            "/alterna/v1/signatures",
            json={
                "signature_request_id": solicitud.id_firma,
                "document_digest": solicitud.documento_hash,
                "document_version": solicitud.version_documento,
                "signer": solicitud.firmante_id,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        estado = ESTADOS.get(cuerpo["status"])
        if estado is None:
            # Un estado desconocido no se toma como firmado: queda pendiente y se reintenta.
            return ResultadoFirma(estado="pendiente", motivo="estado_desconocido_del_proveedor")
        return ResultadoFirma(
            estado=estado,
            referencia=cuerpo.get("signature_id"),
            firmada_en=cuerpo.get("signed_at"),
        )

    def sondear(self) -> None:
        """Sonda de recuperación del circuito: nunca crea una firma de verdad."""
        respuesta = self._cliente.get("/alterna/v1/status", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
