"""El stub de notificaciones: idempotente por referencia, registra lo enviado y falla por modos."""

import base64
import hashlib

from fastapi.testclient import TestClient

from app.main import create_app

AVISO = {
    "referencia_comercio": "poliza-emitida-pz-1",
    "plantilla": "poliza-emitida",
    "destinatario_id": "usuario-1",
    "datos": {"numero_poliza": "SOL-2026-000001", "enlace_documento": "/polizas/pz-1/documento"},
}


def test_envia_y_devuelve_la_referencia() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/notificaciones", json=AVISO)

    assert r.status_code == 200
    assert r.json()["estado"] == "enviada"
    assert r.json()["referencia"].startswith("nt-")


def test_repetir_la_referencia_no_envia_otro_aviso() -> None:
    with TestClient(create_app()) as client:
        primero = client.post("/v1/notificaciones", json=AVISO).json()
        segundo = client.post("/v1/notificaciones", json=AVISO).json()
        enviados = client.get("/control/enviados").json()

    assert primero == segundo
    assert len(enviados) == 1  # se avisó una sola vez


def test_lo_enviado_lleva_la_plantilla_el_id_opaco_y_los_datos() -> None:
    with TestClient(create_app()) as client:
        client.post("/v1/notificaciones", json=AVISO)
        enviado = client.get("/control/enviados").json()[0]

    assert enviado["plantilla"] == "poliza-emitida"
    assert enviado["destinatario_id"] == "usuario-1"
    assert enviado["datos"]["numero_poliza"] == "SOL-2026-000001"


def test_modo_error_responde_503_y_no_registra_el_aviso() -> None:
    with TestClient(create_app()) as client:
        caida = client.post("/v1/notificaciones?modo=error", json=AVISO)
        enviados = client.get("/control/enviados").json()

    assert caida.status_code == 503
    assert enviados == []


def test_modo_lento_registra_el_aviso_antes_de_la_espera() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/notificaciones?modo=lento", json=AVISO)
        enviados = client.get("/control/enviados").json()

    assert r.status_code == 200
    assert len(enviados) == 1  # igual quedó enviado aunque el ACL corte a los 700 ms


def test_el_estado_obedece_el_modo_y_no_envia() -> None:
    with TestClient(create_app()) as client:
        sano = client.get("/v1/estado")
        error = client.get("/v1/estado?modo=error")
        enviados = client.get("/control/enviados").json()

    assert sano.status_code == 200
    assert error.status_code == 503
    assert enviados == []  # sondear nunca manda un aviso


def test_un_modo_invalido_se_rechaza() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/control/modo", json={"modo": "rechaza"}).status_code == 422
        assert client.post("/v1/notificaciones?modo=rechaza", json=AVISO).status_code == 400


# ---------- contacto y PDF adjunto (HU-W03, bloque 8, W03-16)

CORREO = "maria.gomez@ejemplo.co"
TELEFONO = "+573001234567"
PDF = b"%PDF-1.4 contenido-secreto-de-la-poliza prima 61200"
ADJUNTO = {
    "nombre": "poliza-SOL-2026-000001.pdf",
    "tipo": "application/pdf",
    "contenido_base64": base64.b64encode(PDF).decode("ascii"),
}


def test_un_aviso_por_correo_con_el_pdf_registra_solo_su_resumen() -> None:
    with TestClient(create_app()) as client:
        r = client.post(
            "/v1/notificaciones", json={**AVISO, "correo": CORREO, "adjuntos": [ADJUNTO]}
        )
        enviado = client.get("/control/enviados").json()[0]

    assert r.status_code == 200 and r.json()["estado"] == "enviada"
    assert enviado["canal"] == "correo"
    assert enviado["destino_huella"] == hashlib.sha256(CORREO.encode()).hexdigest()[:12]
    assert enviado["adjuntos"] == [
        {
            "nombre": "poliza-SOL-2026-000001.pdf",
            "tipo": "application/pdf",
            "bytes": len(PDF),
            "sha256": hashlib.sha256(PDF).hexdigest(),
            "es_pdf": True,
        }
    ]


def test_el_stub_no_guarda_ni_el_correo_ni_el_contenido() -> None:
    with TestClient(create_app()) as client:
        client.post("/v1/notificaciones", json={**AVISO, "correo": CORREO, "adjuntos": [ADJUNTO]})
        todo = client.get("/control/enviados").text

    assert CORREO not in todo and "maria.gomez" not in todo
    assert ADJUNTO["contenido_base64"] not in todo and "contenido-secreto" not in todo


def test_un_aviso_por_sms_no_lleva_adjuntos() -> None:
    with TestClient(create_app()) as client:
        client.post("/v1/notificaciones", json={**AVISO, "telefono": TELEFONO})
        enviado = client.get("/control/enviados").json()[0]

    assert enviado["canal"] == "sms"
    assert enviado["adjuntos"] == []
    assert enviado["destino_huella"] == hashlib.sha256(TELEFONO.encode()).hexdigest()[:12]


def test_un_aviso_sin_contacto_sigue_igual_que_antes() -> None:
    with TestClient(create_app()) as client:
        r = client.post("/v1/notificaciones", json=AVISO)
        enviado = client.get("/control/enviados").json()[0]

    assert r.status_code == 200
    assert (
        enviado["canal"] is None and enviado["destino_huella"] is None and enviado["adjuntos"] == []
    )


def test_repetir_el_aviso_con_pdf_no_lo_envia_dos_veces() -> None:
    cuerpo = {**AVISO, "correo": CORREO, "adjuntos": [ADJUNTO]}
    with TestClient(create_app()) as client:
        primero = client.post("/v1/notificaciones", json=cuerpo).json()
        segundo = client.post("/v1/notificaciones", json=cuerpo).json()
        enviados = client.get("/control/enviados").json()

    assert primero == segundo
    assert len(enviados) == 1


def test_un_adjunto_sin_correo_se_rechaza_y_no_se_registra() -> None:
    with TestClient(create_app()) as client:
        r = client.post(
            "/v1/notificaciones", json={**AVISO, "telefono": TELEFONO, "adjuntos": [ADJUNTO]}
        )
        enviados = client.get("/control/enviados").json()

    assert r.status_code == 422
    assert enviados == []


def test_un_adjunto_que_no_es_base64_se_rechaza() -> None:
    malo = {**ADJUNTO, "contenido_base64": "esto no es base64!"}
    with TestClient(create_app()) as client:
        r = client.post("/v1/notificaciones", json={**AVISO, "correo": CORREO, "adjuntos": [malo]})
        enviados = client.get("/control/enviados").json()

    assert r.status_code == 422
    assert enviados == []


def test_el_resumen_dice_si_el_adjunto_no_es_un_pdf() -> None:
    texto = {**ADJUNTO, "contenido_base64": base64.b64encode(b"esto no es un pdf").decode("ascii")}
    with TestClient(create_app()) as client:
        client.post("/v1/notificaciones", json={**AVISO, "correo": CORREO, "adjuntos": [texto]})
        enviado = client.get("/control/enviados").json()[0]

    assert enviado["adjuntos"][0]["es_pdf"] is False
