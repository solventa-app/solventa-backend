"""Segundo proveedor simulado (EC031): otra API sobre la misma memoria de cobros y modos."""

import uuid

from fastapi.testclient import TestClient

from app.main import create_app


def _cliente() -> TestClient:
    return TestClient(create_app())


def _orden(referencia: str | None = None) -> dict:
    return {
        "reference": referencia or f"alt-{uuid.uuid4()}",
        "policy": "pol-1",
        "amount": "150000.00",
        "currency": "COP",
        "payment_token": "tok_abc123",
    }


def test_cobra_y_responde_con_los_nombres_del_segundo_proveedor() -> None:
    r = _cliente().post("/alterna/v2/charges", json=_orden())

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["status"] == "APPROVED"
    assert cuerpo["transaction_id"].startswith("pg-")
    assert cuerpo["decline_code"] is None


def test_repetir_la_referencia_no_cobra_dos_veces() -> None:
    cliente, orden = _cliente(), _orden()

    primero = cliente.post("/alterna/v2/charges", json=orden).json()
    segundo = cliente.post("/alterna/v2/charges", json=orden).json()

    assert primero["transaction_id"] == segundo["transaction_id"]


def test_modo_rechaza_responde_declined_con_el_motivo() -> None:
    cliente = _cliente()
    cliente.post("/control/modo", json={"modo": "rechaza"})

    cuerpo = cliente.post("/alterna/v2/charges", json=_orden()).json()

    assert cuerpo["status"] == "DECLINED"
    assert cuerpo["decline_code"] == "fondos_insuficientes"


def test_consultar_un_cobro_registrado_y_uno_que_no_existe() -> None:
    cliente, orden = _cliente(), _orden()
    cliente.post("/alterna/v2/charges", json=orden)

    assert cliente.get(f"/alterna/v2/charges/{orden['reference']}").json()["status"] == "APPROVED"
    assert cliente.get("/alterna/v2/charges/no-existe").status_code == 404


def test_modo_error_y_estado_obedecen_el_modo_simulado() -> None:
    cliente = _cliente()
    cliente.post("/control/modo", json={"modo": "error"})

    assert cliente.post("/alterna/v2/charges", json=_orden()).status_code == 503
    assert cliente.get("/alterna/v2/status").status_code == 503

    cliente.post("/control/modo", json={"modo": "sano"})
    assert cliente.get("/alterna/v2/status").json() == {"ok": True}


def test_las_dos_apis_comparten_la_memoria_de_cobros() -> None:
    cliente = _cliente()
    cliente.post(
        "/v1/cobros",
        json={
            "referencia_comercio": "compartida-1",
            "poliza_id": "pol-1",
            "monto": "1.00",
            "moneda": "COP",
            "token_medio_pago": "tok",
        },
    )

    assert cliente.get("/alterna/v2/charges/compartida-1").json()["status"] == "APPROVED"
