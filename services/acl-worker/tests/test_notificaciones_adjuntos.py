"""Contacto y PDF adjunto en el aviso (HU-W03, bloque 8, W03-16): lo que viaja al proveedor, las
reglas del borde HTTP y que ni el correo ni el contenido lleguen a logs ni a mensajes de error."""

import base64
import json
import logging
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.notificaciones import AdaptadorNotificaciones
from app.application.configuracion import Configuracion
from app.domain.puertos import Adjunto, ResultadoNotificacion, SolicitudNotificacion
from app.main import create_app

CORREO = "maria.gomez@ejemplo.co"
TELEFONO = "+573001234567"
PDF = b"%PDF-1.4 contenido-secreto-de-la-poliza prima 61200"
PDF_B64 = base64.b64encode(PDF).decode("ascii")

# Los proveedores apuntan a un puerto sin servicio (falla rápida): igual que `test_main.py`.
CONFIG = Configuracion(
    redis_url="redis://localhost:6399/0",
    open_finance_url="http://127.0.0.1:1",
    open_data_url="http://127.0.0.1:1",
    pasarela_url="http://127.0.0.1:1",
    firma_url="http://127.0.0.1:1",
    kyc_url="http://127.0.0.1:1",
    notificaciones_url="http://127.0.0.1:1",
    timeout_s=0.2,
    breaker_umbral_fallos=3,
    breaker_ttl_segundos=5,
    sonda_intervalo_segundos=60,
    cache_ttl_segundos=300,
    cola_reconciliacion_redis_url="redis://localhost:6399/2",
)

BASE = {
    "id_notificacion": "poliza-emitida-pz-1",
    "plantilla": "poliza-emitida",
    "destinatario_id": "usuario-1",
    "datos": {"numero_poliza": "SOL-2026-000001"},
}
ADJUNTO = {
    "nombre": "poliza-SOL-2026-000001.pdf",
    "tipo": "application/pdf",
    "contenido_base64": PDF_B64,
}


def _enviar(config: Configuracion = CONFIG, **extra):
    with TestClient(create_app(config)) as client:
        return client.post("/notificaciones/enviar", json={**BASE, **extra})


# ---------- lo que viaja al proveedor (adaptador)


def _cuerpo_enviado(solicitud: SolicitudNotificacion) -> dict:
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json={"estado": "enviada", "referencia": "nt-1"})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    AdaptadorNotificaciones("http://p", 0.7, cliente=cliente).enviar(solicitud)
    return json.loads(vistas[0].content)


def _solicitud(**extra) -> SolicitudNotificacion:
    return SolicitudNotificacion(
        id_notificacion="poliza-emitida-pz-1",
        plantilla="poliza-emitida",
        destinatario_id="usuario-1",
        datos={"numero_poliza": "SOL-2026-000001"},
        **extra,
    )


def test_el_adaptador_manda_el_correo_y_el_pdf_adjunto() -> None:
    cuerpo = _cuerpo_enviado(
        _solicitud(correo=CORREO, adjuntos=[Adjunto("poliza.pdf", "application/pdf", PDF_B64)])
    )

    assert cuerpo["correo"] == CORREO
    assert cuerpo["adjuntos"] == [
        {"nombre": "poliza.pdf", "tipo": "application/pdf", "contenido_base64": PDF_B64}
    ]
    assert "telefono" not in cuerpo


def test_el_adaptador_manda_solo_el_telefono_sin_adjuntos() -> None:
    cuerpo = _cuerpo_enviado(_solicitud(telefono=TELEFONO))

    assert cuerpo["telefono"] == TELEFONO
    assert "correo" not in cuerpo and "adjuntos" not in cuerpo


def test_sin_contacto_ni_adjunto_el_cuerpo_es_el_de_siempre() -> None:
    assert set(_cuerpo_enviado(_solicitud())) == {
        "referencia_comercio",
        "plantilla",
        "destinatario_id",
        "datos",
    }


# ---------- reglas del borde HTTP


def test_una_solicitud_valida_pasa_y_con_el_proveedor_caido_queda_pendiente() -> None:
    r = _enviar(correo=CORREO, adjuntos=[ADJUNTO])

    assert r.status_code == 200
    assert r.json()["estado"] == "pendiente"  # el proveedor de pruebas no existe: se degrada


def test_el_servicio_recibe_el_contacto_y_el_adjunto_sin_cambios() -> None:
    capturadas: list[SolicitudNotificacion] = []

    class Espia:
        def enviar(self, solicitud: SolicitudNotificacion) -> ResultadoNotificacion:
            capturadas.append(solicitud)
            return ResultadoNotificacion(estado="enviada", referencia="nt-1")

    with TestClient(create_app(CONFIG)) as client:
        client.app.state.servicio_notificaciones = Espia()
        r = client.post(
            "/notificaciones/enviar", json={**BASE, "correo": CORREO, "adjuntos": [ADJUNTO]}
        )

    assert r.json() == {"estado": "enviada", "referencia": "nt-1", "motivo": None}
    solicitud = capturadas[0]
    assert solicitud.correo == CORREO and solicitud.telefono is None
    assert solicitud.adjuntos == [Adjunto(ADJUNTO["nombre"], "application/pdf", PDF_B64)]


def test_solo_telefono_sin_adjuntos_pasa() -> None:
    assert _enviar(telefono=TELEFONO).status_code == 200


@pytest.mark.parametrize(
    ("extra", "detalle"),
    [
        ({"adjuntos": [ADJUNTO]}, "solo se envía por correo"),
        ({"telefono": TELEFONO, "adjuntos": [ADJUNTO]}, "solo se envía por correo"),
        (
            {"correo": CORREO, "adjuntos": [{**ADJUNTO, "tipo": "text/html"}]},
            "Tipo de adjunto no permitido",
        ),
        (
            {"correo": CORREO, "adjuntos": [{**ADJUNTO, "contenido_base64": "no es base64!"}]},
            "no es base64 válido",
        ),
        ({"correo": CORREO, "adjuntos": [{**ADJUNTO, "contenido_base64": ""}]}, "está vacío"),
        ({"correo": CORREO, "adjuntos": [ADJUNTO] * 4}, "Máximo 3 adjuntos"),
    ],
)
def test_las_solicitudes_mal_armadas_son_422_sin_repetir_el_contenido(
    extra: dict, detalle: str
) -> None:
    r = _enviar(**extra)

    assert r.status_code == 422
    assert detalle in r.json()["detail"]
    # El mensaje de error no devuelve ni el correo ni el contenido del archivo.
    assert CORREO not in r.text and "contenido-secreto" not in r.text and PDF_B64 not in r.text


def test_un_adjunto_que_pasa_el_tope_es_422() -> None:
    r = _enviar(
        replace(CONFIG, adjunto_maximo_bytes=len(PDF) - 1), correo=CORREO, adjuntos=[ADJUNTO]
    )

    assert r.status_code == 422
    assert "tamaño permitido" in r.json()["detail"]


def test_un_adjunto_justo_en_el_tope_pasa() -> None:
    r = _enviar(replace(CONFIG, adjunto_maximo_bytes=len(PDF)), correo=CORREO, adjuntos=[ADJUNTO])

    assert r.status_code == 200


def test_el_tope_sale_del_entorno(monkeypatch) -> None:
    monkeypatch.setenv("ADJUNTO_MAXIMO_BYTES", "1234")

    assert Configuracion.desde_entorno().adjunto_maximo_bytes == 1234


def test_el_tope_por_omision_alcanza_para_un_pdf_de_poliza() -> None:
    assert Configuracion.desde_entorno().adjunto_maximo_bytes >= 1_000_000


# ---------- datos personales fuera de los logs


def test_los_logs_no_llevan_el_correo_el_telefono_ni_el_contenido(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        _enviar(
            correo=CORREO, telefono=TELEFONO, adjuntos=[ADJUNTO]
        )  # proveedor caído: se loguea el fallo
        _enviar(adjuntos=[ADJUNTO])  # 422

    for dato in (
        CORREO,
        "maria.gomez",
        TELEFONO,
        "3001234567",
        PDF_B64,
        "contenido-secreto",
        "poliza-emitida-pz-1",
    ):
        assert dato not in caplog.text
