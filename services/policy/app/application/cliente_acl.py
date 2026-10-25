"""Cliente HTTP hacia el ACL Worker (`POST {ACL_URL}/firma/firmar`).

POLICY **nunca** llama al proveedor de firma directamente (regla de arquitectura #2). Si la llamada
al ACL falla por cualquier motivo (red, timeout, 5xx), no se propaga la excepción: se devuelve
`ResultadoFirma(estado="pendiente", motivo="acl_no_disponible")` y POLICY ofrece reintentar (regla
#4: nunca un 5xx crudo)."""

import logging
from dataclasses import dataclass, field
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


@dataclass
class AdjuntoAviso:
    """Un archivo del aviso (el PDF de la póliza), con el contenido en base64."""

    nombre: str
    tipo: str
    contenido_base64: str


@dataclass
class SolicitudNotificacion:
    """Desde el bloque 8 (W03-16) el aviso lleva el contacto del cliente y el PDF: son datos
    personales y financieros que POLICY no guarda y que no se escriben en logs. Solo el correo
    lleva adjuntos."""

    id_notificacion: str  # idempotente: repetirlo no manda dos avisos
    plantilla: str
    destinatario_id: str  # id opaco del usuario
    datos: dict[str, str]
    correo: str | None = None
    telefono: str | None = None
    adjuntos: list[AdjuntoAviso] = field(default_factory=list)


@dataclass
class ResultadoNotificacion:
    # "enviada" | "pendiente" (se reintenta) | "rechazada" (el ACL no aceptó la solicitud:
    # reintentar no la arregla).
    estado: str
    referencia: str | None = None
    motivo: str | None = None


class PuertoClienteAcl(Protocol):
    def firmar(self, solicitud: SolicitudFirma) -> ResultadoFirma: ...


class PuertoNotificador(Protocol):
    def notificar(self, solicitud: SolicitudNotificacion) -> ResultadoNotificacion: ...


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

    def notificar(self, solicitud: SolicitudNotificacion) -> ResultadoNotificacion:
        """Avisa al cliente por el ACL Worker. Como la firma, nunca lanza: ante una falla devuelve
        `pendiente` y quien llama reintenta."""
        try:
            cuerpo_envio: dict = {
                "id_notificacion": solicitud.id_notificacion,
                "plantilla": solicitud.plantilla,
                "destinatario_id": solicitud.destinatario_id,
                "datos": solicitud.datos,
            }
            if solicitud.correo:
                cuerpo_envio["correo"] = solicitud.correo
            if solicitud.telefono:
                cuerpo_envio["telefono"] = solicitud.telefono
            if solicitud.adjuntos:
                cuerpo_envio["adjuntos"] = [
                    {"nombre": a.nombre, "tipo": a.tipo, "contenido_base64": a.contenido_base64}
                    for a in solicitud.adjuntos
                ]
            respuesta = self._cliente.post("/notificaciones/enviar", json=cuerpo_envio)
            if respuesta.status_code == 422:
                # El ACL rechazó la solicitud (p. ej. un adjunto que pasa el tope): reintentar la
                # misma solicitud da el mismo 422, así que no se trata como "no disponible".
                logger.warning("el ACL rechazó el aviso por una solicitud inválida")
                return ResultadoNotificacion(estado="rechazada", motivo="solicitud_invalida")
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            resultado = ResultadoNotificacion(
                estado=cuerpo["estado"],
                referencia=cuerpo.get("referencia"),
                motivo=cuerpo.get("motivo"),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            logger.warning("ACL no disponible al notificar error=%s", type(error).__name__)
            return ResultadoNotificacion(estado="pendiente", motivo="acl_no_disponible")
        return resultado

    def cerrar(self) -> None:
        self._cliente.close()
