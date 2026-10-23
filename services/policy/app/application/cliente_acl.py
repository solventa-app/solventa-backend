"""Cliente HTTP hacia el ACL Worker (`POST {ACL_URL}/firma/firmar`).

POLICY **nunca** llama al proveedor de firma directamente (regla de arquitectura #2). Si la llamada
al ACL falla por cualquier motivo (red, timeout, 5xx), no se propaga la excepción: se devuelve
`ResultadoFirma(estado="pendiente", motivo="acl_no_disponible")` y POLICY ofrece reintentar (regla
#4: nunca un 5xx crudo)."""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger("policy.cliente_acl")


@dataclass
class SolicitudFirma:
    id_firma: str
    documento_hash: str
    version_documento: str
    firmante_id: str


@dataclass
class ResultadoFirma:
    estado: str  # "firmada" | "pendiente"
    referencia: str | None = None
    firmada_en: str | None = None
    motivo: str | None = None


class PuertoClienteAcl(Protocol):
    def firmar(self, solicitud: SolicitudFirma) -> ResultadoFirma: ...


class ClienteAcl:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def firmar(self, solicitud: SolicitudFirma) -> ResultadoFirma:
        try:
            respuesta = self._cliente.post(
                "/firma/firmar",
                json={
                    "id_firma": solicitud.id_firma,
                    "documento_hash": solicitud.documento_hash,
                    "version_documento": solicitud.version_documento,
                    "firmante_id": solicitud.firmante_id,
                },
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            # Una respuesta sin `estado` (o que no es un objeto) se trata como no disponible.
            resultado = ResultadoFirma(
                estado=cuerpo["estado"],
                referencia=cuerpo.get("referencia"),
                firmada_en=cuerpo.get("firmada_en"),
                motivo=cuerpo.get("motivo"),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            logger.warning("ACL no disponible al firmar error=%s", type(error).__name__)
            return ResultadoFirma(estado="pendiente", motivo="acl_no_disponible")
        return resultado

    def cerrar(self) -> None:
        self._cliente.close()
