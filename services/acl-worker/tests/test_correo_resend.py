"""Correo real con Resend (HU-W03): lo que viaja a la API, la idempotencia, cómo se leen los errores
y que el correo y el PDF no lleguen a logs. Sin red: `httpx.MockTransport`."""

import base64
import json
import logging

import httpx
import pytest

from app.adapters.correo_resend import AdaptadorCorreoResend, componer
from app.application.configuracion import Configuracion
from app.domain.puertos import Adjunto, SolicitudNotificacion

CORREO = "maria.gomez@ejemplo.co"
PDF_B64 = base64.b64encode(b"%PDF-1.4 contenido-secreto prima 61200").decode("ascii")


def _adaptador(manejador) -> tuple[AdaptadorCorreoResend, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def transporte(request: httpx.Request) -> httpx.Response:
        pedidos.append(request)
        return manejador(request)

    cliente = httpx.Client(
        base_url="https://api.resend.test",
        headers={"Authorization": "Bearer re_prueba"},
        transport=httpx.MockTransport(transporte),
    )
    return AdaptadorCorreoResend(
        "re_prueba", "Solventa <avisos@solventa.test>", 0.7, cliente=cliente
    ), pedidos


def _solicitud(**extra) -> SolicitudNotificacion:
    base = {
        "id_notificacion": "poliza-emitida-pz-1",
        "plantilla": "poliza-emitida",
        "destinatario_id": "usuario-1",
        "datos": {"numero_poliza": "SOL-2026-000001"},
        "correo": CORREO,
    }
    return SolicitudNotificacion(**{**base, **extra})


def test_envia_el_correo_con_el_pdf_y_la_llave_de_idempotencia() -> None:
    adaptador, pedidos = _adaptador(lambda r: httpx.Response(200, json={"id": "re-123"}))

    resultado = adaptador.enviar(
        _solicitud(adjuntos=[Adjunto("poliza.pdf", "application/pdf", PDF_B64)])
    )

    assert resultado.estado == "enviada"
    assert resultado.referencia == "re-123"
    pedido = pedidos[0]
    assert pedido.url.path == "/emails"
    assert pedido.headers["Idempotency-Key"] == "poliza-emitida-pz-1"
    assert pedido.headers["Authorization"] == "Bearer re_prueba"
    cuerpo = json.loads(pedido.content)
    assert cuerpo["to"] == [CORREO]
    assert cuerpo["from"] == "Solventa <avisos@solventa.test>"
    assert "SOL-2026-000001" in cuerpo["subject"]
    assert cuerpo["attachments"] == [
        {"filename": "poliza.pdf", "content": PDF_B64, "content_type": "application/pdf"}
    ]


def test_sin_correo_no_llama_a_resend_y_queda_rechazada() -> None:
    """P. ej. un cliente sin contacto en AUTH: POLICY lo da por omitido."""
    adaptador, pedidos = _adaptador(lambda r: httpx.Response(200, json={"id": "x"}))

    resultado = adaptador.enviar(_solicitud(correo=None, plantilla="poliza-emitida"))

    assert (resultado.estado, resultado.motivo) == ("rechazada", "sin_correo")
    assert pedidos == []


def test_una_plantilla_que_no_existe_queda_rechazada_sin_llamar() -> None:
    adaptador, pedidos = _adaptador(lambda r: httpx.Response(200, json={"id": "x"}))

    resultado = adaptador.enviar(_solicitud(plantilla="no-existe"))

    assert (resultado.estado, resultado.motivo) == ("rechazada", "plantilla_desconocida")
    assert pedidos == []


@pytest.mark.parametrize("codigo", [429, 500, 503])
def test_429_y_5xx_lanzan_para_que_el_circuito_y_el_reintento_decidan(codigo) -> None:
    adaptador, _ = _adaptador(lambda r: httpx.Response(codigo, json={"name": "error"}))

    with pytest.raises(httpx.HTTPStatusError):
        adaptador.enviar(_solicitud())


def test_otro_4xx_queda_rechazada_con_el_nombre_del_error_y_sin_el_mensaje() -> None:
    adaptador, _ = _adaptador(
        lambda r: httpx.Response(
            422, json={"name": "validation_error", "message": f"Invalid `to` field: {CORREO}"}
        )
    )

    resultado = adaptador.enviar(_solicitud())

    assert (resultado.estado, resultado.motivo) == ("rechazada", "validation_error")
    assert CORREO not in repr(resultado)


def test_un_4xx_sin_json_queda_rechazada_con_el_codigo() -> None:
    adaptador, _ = _adaptador(lambda r: httpx.Response(403, text="forbidden"))

    resultado = adaptador.enviar(_solicitud())

    assert (resultado.estado, resultado.motivo) == ("rechazada", "http_403")


def test_no_escribe_el_correo_ni_el_pdf_en_los_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    adaptador, _ = _adaptador(lambda r: httpx.Response(200, json={"id": "re-1"}))

    adaptador.enviar(_solicitud(adjuntos=[Adjunto("p.pdf", "application/pdf", PDF_B64)]))

    assert CORREO not in caplog.text
    assert PDF_B64 not in caplog.text


def test_la_sonda_no_envia_y_un_401_cuenta_como_arriba() -> None:
    adaptador, pedidos = _adaptador(
        lambda r: httpx.Response(401, json={"name": "restricted_api_key"})
    )

    adaptador.sondear()  # no lanza

    assert [p.method for p in pedidos] == ["GET"]
    assert pedidos[0].url.path == "/domains"


def test_la_sonda_lanza_si_resend_esta_caido() -> None:
    adaptador, _ = _adaptador(lambda r: httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        adaptador.sondear()


def test_el_enlace_y_los_datos_van_escapados_en_el_html_y_planos_en_el_texto() -> None:
    asunto, html, texto = componer(
        "enlace-firma", {"enlace": "https://app.solventa.co/firma#t=a&b", "minutos": "30"}
    )

    assert asunto == "Firma tu póliza de Solventa"
    assert 'href="https://app.solventa.co/firma#t=a&amp;b"' in html
    assert "https://app.solventa.co/firma#t=a&b" in texto
    assert "<" not in texto


def test_el_codigo_llega_en_el_correo() -> None:
    _, html, texto = componer("codigo-verificacion", {"codigo": "626506", "minutos": "10"})

    assert "<b>626506</b>" in html
    assert "626506" in texto and "10 minutos" in texto


def _limpiar(monkeypatch) -> None:
    for nombre in ("NOTIFICACIONES_PROVEEDOR", "RESEND_API_KEY", "CORREO_REMITENTE"):
        monkeypatch.delenv(nombre, raising=False)


def test_por_defecto_el_proveedor_es_el_simulado(monkeypatch) -> None:
    _limpiar(monkeypatch)

    assert Configuracion.desde_entorno().proveedor_notificaciones == "simulado"


@pytest.mark.parametrize("llave", ["", "pendiente-de-cargar"])
def test_resend_sin_la_llave_no_arranca(monkeypatch, llave) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("NOTIFICACIONES_PROVEEDOR", "resend")
    monkeypatch.setenv("RESEND_API_KEY", llave)

    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        Configuracion.desde_entorno()


def test_un_proveedor_desconocido_no_arranca(monkeypatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("NOTIFICACIONES_PROVEEDOR", "sendgrid")

    with pytest.raises(ValueError, match="NOTIFICACIONES_PROVEEDOR"):
        Configuracion.desde_entorno()


def test_resend_con_su_llave_y_remitente(monkeypatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("NOTIFICACIONES_PROVEEDOR", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_abc")
    monkeypatch.setenv("CORREO_REMITENTE", "Solventa <avisos@solventa.co>")

    config = Configuracion.desde_entorno()

    assert (config.proveedor_notificaciones, config.resend_api_key, config.correo_remitente) == (
        "resend",
        "re_abc",
        "Solventa <avisos@solventa.co>",
    )
