"""Adaptador de notificaciones: traducción pura a HTTP contra `stubs/notificaciones` (HU-W03,
CA-W03-08). Implementa `PuertoNotificaciones`.

Solo envía la plantilla, el identificador opaco del usuario y los datos de la plantilla (número de
póliza y ruta del documento): nunca un correo, un teléfono ni el contenido del documento (regla de
arquitectura #6). El proveedor resuelve el contacto con su propia base."""

import httpx

from app.domain.puertos import ResultadoNotificacion, SolicitudNotificacion


class AdaptadorNotificaciones:
    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        cliente: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._cliente = cliente or httpx.Client(base_url=base_url.rstrip("/"), auth=auth)

    def enviar(
        self, solicitud: SolicitudNotificacion, timeout_s: float | None = None
    ) -> ResultadoNotificacion:
        timeout = timeout_s if timeout_s is not None else self._timeout_s
        respuesta = self._cliente.post(
            "/v1/notificaciones",
            json={
                "referencia_comercio": solicitud.id_notificacion,
                "plantilla": solicitud.plantilla,
                "destinatario_id": solicitud.destinatario_id,
                "datos": solicitud.datos,
            },
            timeout=timeout,
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        return ResultadoNotificacion(
            estado=cuerpo["estado"],
            referencia=cuerpo.get("referencia"),
            motivo=cuerpo.get("motivo"),
        )

    def sondear(self) -> None:
        """`GET /v1/estado` (obedece el modo simulado) en vez de enviar: sondear la recuperación del
        circuito nunca debe mandar un aviso de verdad. Ver `app/application/sonda.py`."""
        respuesta = self._cliente.get("/v1/estado", timeout=self._timeout_s)
        respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
