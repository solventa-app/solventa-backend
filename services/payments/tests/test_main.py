"""Pruebas del endpoint `POST /cobros` y `GET /polizas/{id}/balance` con
dobles de prueba (sin PostgreSQL ni ACL Worker reales — la verificación con
Postgres y el ACL Worker reales se hace en vivo con `docker compose`, ver
README.md)."""

from fakes import BusEventosFalso, ClienteAclFalso, RepositorioCobrosFalso
from fastapi.testclient import TestClient

from app.application.cliente_acl import ResultadoCobro
from app.application.servicio_cobros import ServicioCobros
from app.main import create_app

ORDEN = {
    "clave_idempotencia": "clave-endpoint-1",
    "poliza_id": "poliza-endpoint-1",
    "monto": "200.00",
    "moneda": "COP",
    "token_medio_pago": "tok_visa_9999",
}


def _cliente(cliente_acl=None) -> tuple[TestClient, ClienteAclFalso]:
    cliente_acl = cliente_acl or ClienteAclFalso()
    servicio = ServicioCobros(RepositorioCobrosFalso(), cliente_acl, BusEventosFalso())
    cliente = TestClient(create_app(servicio_cobros=servicio, reintento=None))
    return cliente, cliente_acl


def test_post_cobros_con_pasarela_sana_termina_cobrado() -> None:
    with _cliente()[0] as client:
        r = client.post("/cobros", json=ORDEN)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "cobrado"
    assert cuerpo["poliza_id"] == ORDEN["poliza_id"]


def test_post_cobros_con_pasarela_caida_queda_pendiente_sin_5xx() -> None:
    resultado = ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")
    cliente_acl = ClienteAclFalso([resultado])
    with _cliente(cliente_acl)[0] as client:
        r = client.post("/cobros", json=ORDEN)

    assert r.status_code == 200  # nunca un 5xx crudo al cliente
    assert r.json()["estado"] == "pendiente"


def test_post_cobros_misma_clave_dos_veces_no_duplica_ni_llama_dos_veces_al_acl() -> None:
    cliente, cliente_acl = _cliente()
    with cliente as client:
        primero = client.post("/cobros", json=ORDEN).json()
        segundo = client.post("/cobros", json=ORDEN).json()

    assert primero["id"] == segundo["id"]
    assert cliente_acl.llamadas == 1


def test_get_balance_suma_solo_cobros_cobrados() -> None:
    with _cliente()[0] as client:
        client.post("/cobros", json=ORDEN)
        r = client.get(f"/polizas/{ORDEN['poliza_id']}/balance")

    assert r.status_code == 200
    assert r.json() == {"poliza_id": ORDEN["poliza_id"], "balance": "200.00"}


def test_get_balance_poliza_sin_cobros_es_cero() -> None:
    with _cliente()[0] as client:
        r = client.get("/polizas/poliza-sin-cobros/balance")

    assert r.json()["balance"] == "0.00"
