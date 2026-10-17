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
    "id_intento_cobro": "clave-endpoint-1",
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


def test_post_cobros_mismo_intento_dos_veces_no_duplica_ni_llama_dos_veces_al_acl() -> None:
    cliente, cliente_acl = _cliente()
    with cliente as client:
        primero = client.post("/cobros", json=ORDEN).json()
        segundo = client.post("/cobros", json=ORDEN).json()

    assert primero["id"] == segundo["id"]
    assert cliente_acl.llamadas == 1


def test_post_cobros_mismo_intento_con_otros_datos_responde_409() -> None:
    cliente, cliente_acl = _cliente()
    with cliente as client:
        client.post("/cobros", json=ORDEN)
        r = client.post("/cobros", json={**ORDEN, "monto": "999.00"})

    assert r.status_code == 409
    assert "otros datos" in r.json()["detail"]
    assert cliente_acl.llamadas == 1


def test_post_cobros_mismo_monto_escrito_distinto_no_es_conflicto() -> None:
    with _cliente()[0] as client:
        primero = client.post("/cobros", json=ORDEN).json()
        r = client.post("/cobros", json={**ORDEN, "monto": "200"})

    assert r.status_code == 200
    assert r.json()["id"] == primero["id"]


def test_post_cobros_sin_id_intento_cobro_responde_422() -> None:
    with _cliente()[0] as client:
        r = client.post("/cobros", json={**ORDEN, "id_intento_cobro": ""})

    assert r.status_code == 422


def test_post_cobros_ya_no_acepta_el_nombre_anterior() -> None:
    orden = {k: v for k, v in ORDEN.items() if k != "id_intento_cobro"}
    with _cliente()[0] as client:
        r = client.post("/cobros", json={**orden, "clave_idempotencia": "clave-vieja-1"})

    assert r.status_code == 422


def test_post_cobros_guarda_y_devuelve_el_cliente_id() -> None:
    with _cliente()[0] as client:
        creado = client.post("/cobros", json={**ORDEN, "cliente_id": "u-cliente-1"}).json()
        leido = client.get(f"/cobros/{creado['id']}").json()

    assert creado["cliente_id"] == "u-cliente-1"
    assert leido["cliente_id"] == "u-cliente-1"


def test_post_cobros_sin_cliente_id_sigue_funcionando() -> None:
    with _cliente()[0] as client:
        r = client.post("/cobros", json=ORDEN)

    assert r.status_code == 200
    assert r.json()["cliente_id"] is None


def test_post_cobros_mismo_intento_con_otro_cliente_responde_409() -> None:
    with _cliente()[0] as client:
        client.post("/cobros", json={**ORDEN, "cliente_id": "u-cliente-1"})
        r = client.post("/cobros", json={**ORDEN, "cliente_id": "u-cliente-2"})

    assert r.status_code == 409


def test_la_respuesta_nunca_trae_el_token_del_medio_de_pago() -> None:
    with _cliente()[0] as client:
        r = client.post("/cobros", json=ORDEN)

    assert "token_medio_pago" not in r.json()
    assert ORDEN["token_medio_pago"] not in r.text


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
        client.post("/cobros", json={**ORDEN, "id_intento_cobro": "k-a", "poliza_id": "POL-A"})
        client.post("/cobros", json={**ORDEN, "id_intento_cobro": "k-b", "poliza_id": "POL-B"})

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
    assert r.json()["id_intento_cobro"] == ORDEN["id_intento_cobro"]


def test_post_reintentar_un_rechazado_lo_vuelve_a_cobrar_y_dice_cuantos_quedan() -> None:
    rechazo = ResultadoCobro(estado="rechazado", motivo="fondos_insuficientes")
    cliente, _ = _cliente(ClienteAclFalso([rechazo, ResultadoCobro(estado="cobrado")]))
    with cliente as client:
        creado = client.post("/cobros", json=ORDEN).json()
        r = client.post(f"/cobros/{creado['id']}/reintentar")

    assert creado["estado"] == "rechazado"
    assert creado["reintentos_rechazo_restantes"] == 3
    assert r.status_code == 200
    assert r.json()["estado"] == "cobrado"
    assert r.json()["reintentos_rechazo"] == 1
    assert r.json()["reintentos_rechazo_restantes"] == 2


def test_post_reintentar_un_rechazado_sin_reintentos_responde_409_con_mensaje() -> None:
    cliente, _ = _cliente(ClienteAclFalso([ResultadoCobro(estado="rechazado", motivo="fondos")]))
    with cliente as client:
        creado = client.post("/cobros", json=ORDEN).json()
        for _ in range(3):
            assert client.post(f"/cobros/{creado['id']}/reintentar").status_code == 200
        r = client.post(f"/cobros/{creado['id']}/reintentar")

    assert r.status_code == 409
    assert "otro medio de pago" in r.json()["detail"]


def test_post_reintentar_inexistente_404() -> None:
    cliente, _ = _cliente()
    with cliente as client:
        assert client.post("/cobros/no-existe/reintentar").status_code == 404
