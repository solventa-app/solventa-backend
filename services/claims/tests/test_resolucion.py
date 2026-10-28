import pytest
from fakes import AHORA, PaymentsFalso, PolicyPolizas, repositorio_con_datos
from fastapi.testclient import TestClient

from app.application.clientes import ServicioNoDisponible
from app.application.servicio_siniestros import (
    MENSAJE_NO_EXISTE,
    MENSAJE_ORDEN_PAGO,
    MENSAJE_PERITO,
    MENSAJE_POLIZA,
    MENSAJE_TEXTO_LARGO,
    MENSAJE_YA_DERIVADO,
    MENSAJE_YA_RESUELTO,
    DependenciaNoDisponible,
    ErrorNegocio,
    ServicioSiniestros,
)
from app.main import create_app


def _servicio(policy=None, payments=None):
    repo = repositorio_con_datos()
    servicio = ServicioSiniestros(
        repo, lambda: AHORA, policy or PolicyPolizas(), payments or PaymentsFalso()
    )
    return servicio, repo


# ---------- Derivar ----------


def test_derivar_deja_el_caso_en_revision_con_su_perito_y_plazo():
    servicio, _ = _servicio()
    s = servicio.derivar("sin-01", "per-01", "  Revisar el bumper  ", "usuario-1")
    assert s["estado"] == "EN_REVISION"
    r = s["resolucion"]
    assert r["tipo"] == "DERIVACION" and r["perito"]["id"] == "per-01"
    assert r["instrucciones"] == "Revisar el bumper"
    assert r["vencimiento_informe"].isoformat() == "2026-11-03T17:00:00-05:00"


@pytest.mark.parametrize(
    ("sid", "perito", "texto", "mensaje"),
    [
        ("sin-99", "per-01", None, MENSAJE_NO_EXISTE),
        ("sin-03", "per-01", None, MENSAJE_YA_RESUELTO),
        ("sin-02", "per-01", None, MENSAJE_YA_DERIVADO),
        ("sin-01", "per-02", None, MENSAJE_PERITO),  # inactivo
        ("sin-01", "per-77", None, MENSAJE_PERITO),
        ("sin-01", "per-01", "x" * 501, MENSAJE_TEXTO_LARGO),
    ],
)
def test_reglas_de_la_derivacion(sid, perito, texto, mensaje):
    servicio, _ = _servicio()
    with pytest.raises(ErrorNegocio, match=mensaje):
        servicio.derivar(sid, perito, texto, None)


def test_un_caso_derivado_todavia_se_puede_rechazar_y_conserva_el_perito():
    servicio, _ = _servicio()
    s = servicio.rechazar("sin-02", "EVIDENCIA_INSUFICIENTE", None, "usuario-1")
    assert s["estado"] == "RECHAZADO"
    assert s["resolucion"]["perito"]["id"] == "per-01"


# ---------- Rechazar ----------


def test_rechazar_guarda_motivo_comentario_y_via_de_recurso():
    servicio, repo = _servicio()
    s = servicio.rechazar("sin-01", "FUERA_DE_COBERTURA", "No aplica", "usuario-1")
    assert s["estado"] == "RECHAZADO"
    assert s["resolucion"]["motivo"] == "FUERA_DE_COBERTURA"
    assert s["resolucion"]["comentario"] == "No aplica"
    assert "Código de Comercio" in s["resolucion"]["via_recurso"]
    assert repo.resoluciones_por["sin-01"]["actor_usuario_id"] == "usuario-1"


def test_no_se_resuelve_dos_veces():
    servicio, _ = _servicio()
    servicio.rechazar("sin-01", "OTRO", None, None)
    with pytest.raises(ErrorNegocio, match=MENSAJE_YA_RESUELTO):
        servicio.aprobar("sin-01", None)


def test_motivo_invalido():
    servicio, _ = _servicio()
    with pytest.raises(ErrorNegocio):
        servicio.rechazar("sin-01", "PORQUE_SI", None, None)


# ---------- Aprobar ----------


def test_aprobar_registra_la_orden_de_pago_antes_de_aprobar():
    payments = PaymentsFalso()
    servicio, _ = _servicio(payments=payments)
    s = servicio.aprobar("sin-01", "usuario-1")
    assert s["estado"] == "APROBADO"
    assert s["resolucion"]["indemnizacion"] == {"valor": "8650000", "moneda": "COP"}
    assert s["resolucion"]["orden_pago_id"] == "ind-sin-01"
    assert s["resolucion"]["metodo_transferencia"] == "ACH Colombia Interbancario"
    assert payments.ordenes["sin-01"]["monto"] == "8650000"


def test_la_indemnizacion_se_acota_a_la_cobertura_de_la_poliza():
    servicio, _ = _servicio(policy=PolicyPolizas(cobertura="5000000"))
    s = servicio.aprobar("sin-01", None)
    assert s["resolucion"]["indemnizacion"]["valor"] == "5000000"


def test_si_payments_no_responde_el_caso_sigue_abierto():
    servicio, repo = _servicio(payments=PaymentsFalso(falla=ServicioNoDisponible("timeout")))
    with pytest.raises(DependenciaNoDisponible, match=MENSAJE_ORDEN_PAGO):
        servicio.aprobar("sin-01", None)
    assert repo.siniestros["sin-01"]["estado"] == "PENDIENTE"
    assert "sin-01" not in repo.resoluciones_por


def test_si_policy_no_responde_no_se_pide_la_orden():
    payments = PaymentsFalso()
    servicio, _ = _servicio(
        policy=PolicyPolizas(falla=ServicioNoDisponible("x")), payments=payments
    )
    with pytest.raises(DependenciaNoDisponible, match=MENSAJE_POLIZA):
        servicio.aprobar("sin-01", None)
    assert payments.ordenes == {}


def test_reintentar_la_aprobacion_no_duplica_la_orden():
    payments = PaymentsFalso()
    servicio, _ = _servicio(payments=payments)
    servicio.aprobar("sin-01", None)
    with pytest.raises(ErrorNegocio, match=MENSAJE_YA_RESUELTO):
        servicio.aprobar("sin-01", None)
    assert len(payments.ordenes) == 1


# ---------- API ----------


def _cliente(**kwargs):
    servicio, _ = _servicio(**kwargs)
    return TestClient(create_app(servicio=servicio))


def test_api_de_resolucion_y_sus_codigos():
    with _cliente() as c:
        r = c.post("/siniestros/sin-01/derivar", json={"perito_id": "per-01"})
        assert r.status_code == 200 and r.json()["estado"] == "EN_REVISION"
        r = c.post("/siniestros/sin-01/derivar", json={"perito_id": "per-01"})
        assert r.status_code == 409 and r.json() == {"detail": MENSAJE_YA_DERIVADO}
        r = c.post("/siniestros/sin-01/aprobar", json={"actor_usuario_id": "u-1"})
        assert r.status_code == 200 and r.json()["estado"] == "APROBADO"
        r = c.post("/siniestros/sin-01/rechazar", json={"motivo": "OTRO"})
        assert r.status_code == 409


def test_api_dependencia_caida_es_503_con_mensaje():
    with _cliente(payments=PaymentsFalso(falla=ServicioNoDisponible("x"))) as c:
        r = c.post("/siniestros/sin-01/aprobar", json={})
        assert r.status_code == 503 and r.json() == {"detail": MENSAJE_ORDEN_PAGO}
