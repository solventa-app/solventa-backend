"""Adaptador de notificaciones por correo con Resend (HU-W03). Implementa `PuertoNotificaciones`
igual que `AdaptadorNotificaciones` (el simulador): cambiar de proveedor es la bandera
`NOTIFICACIONES_PROVEEDOR` (EC031).

`POST https://api.resend.com/emails` con la llave en `Authorization` y el `id_notificacion` como
`Idempotency-Key`: si POLICY reintenta el mismo aviso, Resend no manda otro correo (24 h). El correo
del destinatario y el PDF viajan solo en el cuerpo por HTTPS y nunca se guardan ni se escriben en
logs (regla #6). Resend solo envía correo: un aviso sin `correo` (p. ej. al asesor, cuyo contacto no
guarda AUTH, o un cliente que solo dejó teléfono) responde `rechazada` sin llamar al proveedor, y
POLICY lo da por omitido en vez de reintentarlo para siempre.

Errores: 5xx, 429 y fallas de red lanzan la excepción (el circuito y el reintento deciden y el aviso
queda `pendiente`); cualquier otro 4xx es `rechazada`, porque reintentar lo mismo da lo mismo."""

import html

import httpx

from app.domain.puertos import ResultadoNotificacion, SolicitudNotificacion

URL_RESEND = "https://api.resend.com"

# Asunto y cuerpo por plantilla. `{campo}` sale de `solicitud.datos` (escapado en el HTML).
PLANTILLAS: dict[str, tuple[str, str]] = {
    "codigo-verificacion": (
        "Tu código de verificación de Solventa",
        "Tu código para confirmar tu identidad con tu asesor es <b>{codigo}</b>. "
        "Vence en {minutos} minutos. Si no lo pediste, ignora este correo.",
    ),
    "codigo-registro": (
        "Tu código para crear tu cuenta en Solventa",
        "Tu código para crear tu cuenta en Solventa es <b>{codigo}</b>. "
        "Vence en {minutos} minutos. Si no lo pediste, ignora este correo.",
    ),
    "enlace-firma": (
        "Firma tu póliza de Solventa",
        'Tu asesor preparó tu póliza. <a href="{enlace}">Revisa y firma aquí</a>. '
        "El enlace vence en {minutos} minutos.",
    ),
    "poliza-emitida": (
        "Tu póliza de Solventa {numero_poliza} está emitida",
        "Tu póliza <b>{numero_poliza}</b> quedó emitida. Adjuntamos el documento en PDF.",
    ),
    "poliza-emitida-asesor": (
        "Póliza {numero_poliza} emitida",
        "La póliza <b>{numero_poliza}</b> que vendiste quedó emitida.",
    ),
}
MOTIVO_SIN_CORREO = "sin_correo"
MOTIVO_PLANTILLA_DESCONOCIDA = "plantilla_desconocida"


class _Campos(dict):
    def __missing__(self, clave: str) -> str:
        return ""


def componer(plantilla: str, datos: dict[str, str]) -> tuple[str, str, str] | None:
    """(asunto, html, texto) de la plantilla, o `None` si no existe."""
    if plantilla not in PLANTILLAS:
        return None
    asunto, cuerpo = PLANTILLAS[plantilla]
    escapados = _Campos({k: html.escape(str(v), quote=True) for k, v in datos.items()})
    planos = _Campos({k: str(v) for k, v in datos.items()})
    texto = cuerpo.replace("<b>", "").replace("</b>", "")
    texto = texto.replace('<a href="{enlace}">', "").replace("</a>", ": {enlace}")
    return (
        asunto.format_map(planos),
        f"<p>{cuerpo.format_map(escapados)}</p><p>— Solventa</p>",
        f"{texto.format_map(planos)}\n\n— Solventa",
    )


class AdaptadorCorreoResend:
    def __init__(
        self,
        api_key: str,
        remitente: str,
        timeout_s: float,
        base_url: str = URL_RESEND,
        cliente: httpx.Client | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._remitente = remitente
        self._cliente = cliente or httpx.Client(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"}
        )

    def enviar(
        self, solicitud: SolicitudNotificacion, timeout_s: float | None = None
    ) -> ResultadoNotificacion:
        if not solicitud.correo:
            return ResultadoNotificacion(estado="rechazada", motivo=MOTIVO_SIN_CORREO)
        compuesto = componer(solicitud.plantilla, solicitud.datos)
        if compuesto is None:
            return ResultadoNotificacion(estado="rechazada", motivo=MOTIVO_PLANTILLA_DESCONOCIDA)
        asunto, cuerpo_html, texto = compuesto
        cuerpo: dict = {
            "from": self._remitente,
            "to": [solicitud.correo],
            "subject": asunto,
            "html": cuerpo_html,
            "text": texto,
        }
        if solicitud.adjuntos:
            cuerpo["attachments"] = [
                {"filename": a.nombre, "content": a.contenido_base64, "content_type": a.tipo}
                for a in solicitud.adjuntos
            ]
        respuesta = self._cliente.post(
            "/emails",
            json=cuerpo,
            headers={"Idempotency-Key": solicitud.id_notificacion[:256]},
            timeout=timeout_s if timeout_s is not None else self._timeout_s,
        )
        if respuesta.status_code == 429 or respuesta.status_code >= 500:
            respuesta.raise_for_status()
        if respuesta.is_error:
            # El nombre del error de Resend (p. ej. validation_error), nunca el mensaje: puede
            # repetir el correo del destinatario.
            try:
                motivo = str(respuesta.json().get("name") or "") or f"http_{respuesta.status_code}"
            except (ValueError, AttributeError):
                motivo = f"http_{respuesta.status_code}"
            return ResultadoNotificacion(estado="rechazada", motivo=motivo)
        return ResultadoNotificacion(estado="enviada", referencia=respuesta.json().get("id"))

    def sondear(self) -> None:
        """La sonda nunca envía: basta con que la API responda. Una llave de solo envío recibe 401
        en `GET /domains`, y eso también prueba que Resend está arriba."""
        respuesta = self._cliente.get("/domains", timeout=self._timeout_s)
        if respuesta.status_code == 429 or respuesta.status_code >= 500:
            respuesta.raise_for_status()

    def cerrar(self) -> None:
        self._cliente.close()
