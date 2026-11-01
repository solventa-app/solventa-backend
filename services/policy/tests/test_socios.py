"""Socios de distribución en POLICY (HU-W07): el SOCIO recorre el flujo del intermediario
(código, «Emitir» y enlace) como el ASESOR, su comisión se causa al emitir y sus ventas se
resumen por mes."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_firma_y_pago import CONTACTO, TITULAR, Entorno, _http

from app.application.comisiones import calcular_comision, resumir_ventas
from app.application.servicio_firma_y_pago import Autorizacion
from app.application.servicio_propuestas import TasaInvalida

SOCIO = "socio-demo-1"


@pytest.fixture
def e() -> Entorno:
    return Entorno()


def _hasta_emitida(e: Entorno, oferta_id: str = "oferta-1", rol: str = "SOCIO") -> dict:
    actor = SOCIO if rol == "SOCIO" else "asesor-1"
    propuesta = e.propuestas.aceptar(
        oferta_id, actor, rol, dict(CONTACTO), "0.1000" if rol == "SOCIO" else None
    )
    e.servicio.enviar_codigo(propuesta["id"])
    e.servicio.verificar_codigo(propuesta["id"], e.codigo_enviado())
    emision = e.servicio.emitir(propuesta["id"], rol)
    auth = Autorizacion(enlace_token=e.token_enviado())
    e.firmar_nombre(propuesta["id"], auth)
    sesion = e.servicio.iniciar_pago(propuesta["id"], auth, "http://front/r")["sesion"]
    e.pagos.pagar(sesion.sesion_id)
    e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)
    e.servicio.confirmar_pago(propuesta["id"], auth, sesion.sesion_id)  # repetir no duplica
    return {"propuesta": propuesta, "emision": emision}


def test_el_socio_recorre_el_flujo_del_intermediario_y_el_enlace_va_al_titular(e) -> None:
    resultado = _hasta_emitida(e)

    assert resultado["emision"]["propuesta"]["estado"] == "EN_FIRMA"
    assert resultado["emision"]["enlace"]["correo_enmascarado"] == "c•••@correo.com"
    assert "#t=" not in str(resultado["emision"])  # la URL nunca vuelve al socio (W07-17)
    assert e.repo.obtener(resultado["propuesta"]["id"])["estado"] == "EMITIDA"


def test_la_comision_se_causa_al_emitir_una_sola_vez_con_la_tasa_congelada(e) -> None:
    _hasta_emitida(e)

    (comision,) = e.repo.comisiones
    assert comision["socio_id"] == SOCIO
    assert comision["prima"] == "185000"
    assert comision["comision"] == "18500"  # 185000 × 0,10, sin decimales como la prima


def test_al_socio_no_le_llegan_los_avisos_del_asesor(e) -> None:
    propuesta = _hasta_emitida(e)["propuesta"]
    e.emision.emitir(propuesta["id"])  # la tarea del correo con el PDF

    canales, _ = e.avisos.publicados[-1]
    assert "avisos:asesor:socio-demo-1" not in canales
    assert f"avisos:titular:{TITULAR}" in canales
    plantillas = [s.plantilla for s in e.notificador.solicitudes]
    assert "poliza-emitida" in plantillas  # el titular sí recibe su póliza


def test_una_venta_del_asesor_no_causa_comision(e) -> None:
    _hasta_emitida(e, rol="ASESOR")

    assert e.repo.comisiones == []


def test_la_tasa_del_socio_debe_estar_entre_cero_y_uno(e) -> None:
    with pytest.raises(TasaInvalida):
        e.propuestas.aceptar("oferta-1", SOCIO, "SOCIO", dict(CONTACTO), "1.5")


def test_la_tasa_se_ignora_si_no_es_un_socio(e) -> None:
    e.propuestas.aceptar("oferta-1", "asesor-1", "ASESOR", dict(CONTACTO), "0.1")

    assert e.repo.obtener_por_oferta("oferta-1")["tasa_comision"] is None


def test_las_ventas_del_socio_se_resumen_por_mes(e) -> None:
    _hasta_emitida(e)
    e.propuestas.aceptar("oferta-2", SOCIO, "SOCIO", dict(CONTACTO), "0.1000")
    mes = datetime.now(UTC).strftime("%Y-%m")

    ventas = e.propuestas.ventas_socio(SOCIO, None, None)

    assert ventas["meses"] == [
        {
            "mes": mes,
            "propuestas": 2,
            "emitidas": 1,
            "primas": {"valor": "185000", "moneda": "COP"},
            "comision": {"valor": "18500", "moneda": "COP"},
        }
    ]
    assert ventas["totales"]["emitidas"] == 1
    assert e.propuestas.ventas_socio("otro-socio", None, None)["totales"]["propuestas"] == 0


@pytest.mark.parametrize(
    "prima,tasa,esperada",
    [("185000", "0.10", "18500"), ("123457", "0.08", "9877"), ("45000.50", "0.0750", "3375.04")],
)
def test_calcular_comision_redondea_a_los_decimales_de_la_prima(prima, tasa, esperada) -> None:
    assert calcular_comision(prima, Decimal(tasa)) == esperada


def test_resumir_ventas_sin_movimientos() -> None:
    assert resumir_ventas({}, []) == {
        "meses": [],
        "totales": {
            "propuestas": 0,
            "emitidas": 0,
            "primas": {"valor": "0", "moneda": "COP"},
            "comision": {"valor": "0", "moneda": "COP"},
        },
    }


def test_http_aceptar_como_socio_listar_y_ventas(e) -> None:
    with _http(e) as client:
        creada = client.post(
            "/propuestas",
            json={
                "oferta_id": "oferta-1",
                "actor_usuario_id": SOCIO,
                "actor_rol": "SOCIO",
                "contacto": CONTACTO,
                "tasa_comision": "0.1000",
            },
        )
        mala = client.post(
            "/propuestas",
            json={
                "oferta_id": "oferta-2",
                "actor_usuario_id": SOCIO,
                "actor_rol": "SOCIO",
                "contacto": CONTACTO,
                "tasa_comision": "2",
            },
        )
        pid = creada.json()["id"]
        client.post(f"/propuestas/{pid}/codigo", json={})
        client.post(f"/propuestas/{pid}/codigo/verificar", json={"codigo": e.codigo_enviado()})
        antes = client.get(f"/propuestas/{pid}").json()
        emitida = client.post(f"/propuestas/{pid}/emitir", json={"rol": "SOCIO"})
        despues = client.get(f"/propuestas/{pid}").json()
        del_socio = client.get(
            "/propuestas", params={"actor_usuario_id": SOCIO, "actor_rol": "SOCIO"}
        ).json()
        como_asesor = client.get(
            "/propuestas", params={"actor_usuario_id": SOCIO, "actor_rol": "ASESOR"}
        ).json()
        ventas = client.get(f"/socios/{SOCIO}/ventas")
        al_reves = client.get(
            f"/socios/{SOCIO}/ventas", params={"desde": "2026-10-10", "hasta": "2026-10-01"}
        )

    assert creada.status_code == 200 and creada.json()["actor_rol"] == "SOCIO"
    assert mala.status_code == 422
    assert emitida.status_code == 200 and emitida.json()["propuesta"]["estado"] == "EN_FIRMA"
    # El socio sigue el enlace por su vencimiento, nunca por el token (W07-17).
    assert antes["enlace_vence_en"] is None and despues["enlace_vence_en"] is not None
    assert del_socio["total"] == 1 and como_asesor["total"] == 0
    assert ventas.status_code == 200 and ventas.json()["totales"]["propuestas"] == 1
    assert al_reves.status_code == 422
