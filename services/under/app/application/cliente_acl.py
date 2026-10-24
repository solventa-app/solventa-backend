"""Cliente HTTP hacia el ACL Worker (`POST {ACL_URL}/kyc/verificar`).

UNDER **nunca** llama al proveedor de KYC directamente (regla de arquitectura #2). Si la llamada al
ACL falla por cualquier motivo (red, timeout, 5xx), no se propaga la excepción: se devuelve
`ResultadoKyc(estado="pendiente", motivo="acl_no_disponible")` y la decisión manda la propuesta a
revisión manual (regla #4: nunca un 5xx crudo)."""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger("under.cliente_acl")


@dataclass
class ResultadoKyc:
    estado: str  # "aprobada" | "rechazada" | "pendiente"
    referencia: str | None = None
    motivo: str | None = None


class PuertoClienteAcl(Protocol):
    def verificar_kyc(self, id_verificacion: str, cliente_id: str) -> ResultadoKyc: ...


class ClienteAcl:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def verificar_kyc(self, id_verificacion: str, cliente_id: str) -> ResultadoKyc:
        try:
            respuesta = self._cliente.post(
                "/kyc/verificar",
                json={"id_verificacion": id_verificacion, "cliente_id": cliente_id},
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            # Una respuesta sin `estado` (o que no es un objeto) se trata como no disponible.
            resultado = ResultadoKyc(
                estado=cuerpo["estado"],
                referencia=cuerpo.get("referencia"),
                motivo=cuerpo.get("motivo"),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            logger.warning("ACL no disponible al verificar KYC error=%s", type(error).__name__)
            return ResultadoKyc(estado="pendiente", motivo="acl_no_disponible")
        return resultado

    def cerrar(self) -> None:
        self._cliente.close()
