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


# ---------- back-office: listar y reintentar (KAN-28, CA-W05-01/02)


def test_buscar_cobros_filtra_por_poliza_y_estado() -> None:
    cliente, _ = _cliente(
        ClienteAclFalso([ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")])
    )
    with cliente as client:
        client.post("/cobros", json={**ORDEN, "clave_idempotencia": "k-a", "poliza_id": "POL-A"})
        client.post("/cobros", json={**ORDEN, "clave_idempotencia": "k-b", "poliza_id": "POL-B"})

        por_poliza = client.post("/cobros/buscar", json={"poliza_id": "POL-A"}).json()
        por_estado = client.post("/cobros/buscar", json={"estado": "cobrado"}).json()

    assert [c["poliza_id"] for c in por_poliza] == ["POL-A"]
    assert por_poliza[0]["motivo"] == "pasarela_no_disponible"
    assert por_poliza[0]["actualizado_en"]
    assert por_estado == []


def test_get_cobro_inexistente_404() -> None:
    cliente, _ = _cliente()
    with cliente as client:
        assert client.get("/cobros/no-existe").status_code == 404


def test_post_reintentar_resuelve_un_pendiente() -> None:
    acl = ClienteAclFalso(
        [
            ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible"),
            ResultadoCobro(estado="cobrado", referencia="pg-reintento"),
        ]
    )
    cliente, _ = _cliente(acl)
    with cliente as client:
        creado = client.post("/cobros", json=ORDEN).json()
        r = client.post(f"/cobros/{creado['id']}/reintentar")

    assert r.status_code == 200
    assert r.json()["estado"] == "cobrado"
    assert r.json()["clave_idempotencia"] == ORDEN["clave_idempotencia"]


def test_post_reintentar_un_rechazado_responde_409_con_mensaje() -> None:
    cliente, _ = _cliente(ClienteAclFalso([ResultadoCobro(estado="rechazado", motivo="fondos")]))
    with cliente as client:
        creado = client.post("/cobros", json=ORDEN).json()
        r = client.post(f"/cobros/{creado['id']}/reintentar")

    assert r.status_code == 409
    assert "otro medio de pago" in r.json()["detail"]


def test_post_reintentar_inexistente_404() -> None:
    cliente, _ = _cliente()
    with cliente as client:
        assert client.post("/cobros/no-existe/reintentar").status_code == 404
