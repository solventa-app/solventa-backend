"""Endpoint `POST /decisiones` con un doble del ACL (sin red)."""

from decimal import Decimal

from fakes import AclFalso
from fastapi.testclient import TestClient

from app.application.cliente_acl import ResultadoKyc
from app.application.configuracion import Configuracion
from app.application.servicio_decisiones import ServicioDecisiones
from app.main import create_app

SOLICITUD = {
    "propuesta_id": "propuesta-1",
    "cliente_id": "cliente-opaco",
    "nivel_riesgo": "BAJO",
    "cobertura": {"valor": "200000000", "moneda": "COP"},
}


def _cliente(acl: AclFalso | None = None) -> tuple[TestClient, AclFalso]:
    acl = acl or AclFalso()
    config = Configuracion("http://acl", 1.0, "ninguna")
    servicio = ServicioDecisiones(acl, config.cobertura_maxima_automatica)
    return TestClient(create_app(config=config, servicio=servicio)), acl


def test_health() -> None:
    with _cliente()[0] as client:
        assert client.get("/health").json() == {"servicio": "under", "estado": "ok"}


def test_aprueba_y_pide_el_kyc_con_el_id_de_la_propuesta() -> None:
    cliente, acl = _cliente()
    with cliente as client:
        r = client.post("/decisiones", json=SOLICITUD)

    assert r.status_code == 200
    assert r.json() == {
        "decision": "APROBADA",
        "motivos": [],
        "kyc": {"estado": "aprobada", "referencia": "kyc-1"},
    }
    assert acl.llamadas == [("propuesta-1", "cliente-opaco")]


def test_kyc_rechazado_rechaza() -> None:
    acl = AclFalso(ResultadoKyc(estado="rechazada", motivo="identidad_no_verificada"))
    with _cliente(acl)[0] as client:
        r = client.post("/decisiones", json=SOLICITUD)

    assert r.json()["decision"] == "RECHAZADA"
    assert r.json()["motivos"] == ["KYC_RECHAZADO"]


def test_kyc_sin_respuesta_va_a_revision_manual_y_responde_200_sin_5xx() -> None:
    acl = AclFalso(ResultadoKyc(estado="pendiente", motivo="acl_no_disponible"))
    with _cliente(acl)[0] as client:
        r = client.post("/decisiones", json=SOLICITUD)

    assert r.status_code == 200
    assert r.json()["decision"] == "REVISION_MANUAL"
    assert r.json()["motivos"] == ["KYC_NO_DISPONIBLE"]


def test_decidir_dos_veces_da_la_misma_decision() -> None:
    cliente, _ = _cliente()
    with cliente as client:
        primera = client.post("/decisiones", json=SOLICITUD).json()
        segunda = client.post("/decisiones", json=SOLICITUD).json()

    assert primera == segunda


def test_una_cobertura_que_no_es_un_decimal_responde_422() -> None:
    with _cliente()[0] as client:
        r = client.post(
            "/decisiones", json={**SOLICITUD, "cobertura": {"valor": "mucho", "moneda": "COP"}}
        )

    assert r.status_code == 422


def test_la_cobertura_negativa_responde_422() -> None:
    with _cliente()[0] as client:
        r = client.post(
            "/decisiones", json={**SOLICITUD, "cobertura": {"valor": "-1", "moneda": "COP"}}
        )

    assert r.status_code == 422


def test_el_tope_se_configura() -> None:
    acl = AclFalso()
    servicio = ServicioDecisiones(acl, Decimal("100000000"))
    config = Configuracion("http://acl", 1.0, "ninguna")
    with TestClient(create_app(config=config, servicio=servicio)) as client:
        r = client.post("/decisiones", json=SOLICITUD)

    assert r.json()["decision"] == "REVISION_MANUAL"
    assert r.json()["motivos"] == ["COBERTURA_ALTA"]


def test_los_logs_no_llevan_el_cliente_ni_la_cobertura(caplog) -> None:
    caplog.set_level("INFO")
    with _cliente()[0] as client:
        client.post("/decisiones", json=SOLICITUD)

    assert "cliente-opaco" not in caplog.text
    assert "propuesta-1" not in caplog.text
    assert "200000000" not in caplog.text
