"""Cobros del back-office en el GraphQL del BFF (KAN-28, CA-W05-01/02), con dobles."""

import asyncio
import logging

import httpx
from fakes import PaymentsFalso, clientes_falsos, cobro_de_payments, token_de_prueba
from fastapi.testclient import TestClient

from app.clientes import ClientePaymentsHttp, SolicitudInvalida
from app.configuracion import Configuracion
from app.main import create_app

COBROS = """query($poliza: ID, $estado: EstadoCobro) {
  cobros(polizaId: $poliza, estado: $estado) {
    id polizaId estado monto { valor moneda } actualizadoEn motivo intentos
  }
}"""
REINTENTAR = """mutation($id: ID!) {
  reintentarCobro(cobroId: $id) { id estado motivo intentos }
}"""

CREAR = """mutation($e: CrearCobroInput!) {
  crearCobro(entrada: $e) {
    __typename
    ... on Cobro { id polizaId estado monto { valor moneda } }
    ... on ErrorCobro { mensaje }
  }
}"""
ENTRADA_COBRO = {
    "idIntentoCobro": "intento-bo-1",
    "polizaId": "POL-CREAR-1",
    "monto": {"valor": "80000.00", "moneda": "COP"},
    "tokenMedioPago": "tok_secreto_1",
}


def _cliente(payments: PaymentsFalso) -> TestClient:
    return TestClient(
        create_app(
            config=Configuracion.desde_entorno(), clientes=clientes_falsos(payments=payments)
        )
    )


def _post(client: TestClient, query: str, variables: dict, rol: str = "BACKOFFICE") -> dict:
    r = client.post(
        "/graphql/web",
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {token_de_prueba(rol)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def test_lista_cobros_con_el_formato_del_contrato() -> None:
    pendiente = cobro_de_payments("c-1", "POL-1", "pendiente", motivo="pasarela_no_disponible")
    with _cliente(PaymentsFalso([pendiente])) as client:
        cobros = _post(client, COBROS, {"poliza": "POL-1"})["data"]["cobros"]

    assert cobros == [
        {
            "id": "c-1",
            "polizaId": "POL-1",
            "estado": "PENDIENTE",
            "monto": {"valor": "80000.00", "moneda": "COP"},
            "actualizadoEn": "2026-10-16T12:00:00+00:00",
            "motivo": "pasarela_no_disponible",
            "intentos": 1,
        }
    ]


def test_los_filtros_llegan_a_payments_en_su_formato() -> None:
    payments = PaymentsFalso()
    with _cliente(payments) as client:
        _post(client, COBROS, {"poliza": "POL-9", "estado": "RECHAZADO"})
        _post(client, COBROS, {})

    assert payments.filtros == [("POL-9", "rechazado"), (None, None)]


def test_reintentar_un_pendiente_lo_deja_cobrado() -> None:
    payments = PaymentsFalso([cobro_de_payments("c-1", "POL-1", "pendiente")])
    with _cliente(payments) as client:
        cobro = _post(client, REINTENTAR, {"id": "c-1"})["data"]["reintentarCobro"]

    assert cobro["estado"] == "COBRADO"


def test_reintentar_un_rechazado_da_un_error_claro() -> None:
    payments = PaymentsFalso([cobro_de_payments("c-1", "POL-1", "rechazado")])
    with _cliente(payments) as client:
        cuerpo = _post(client, REINTENTAR, {"id": "c-1"})

    assert "otro medio de pago" in cuerpo["errors"][0]["message"]


def test_reintentar_un_cobro_inexistente() -> None:
    with _cliente(PaymentsFalso()) as client:
        cuerpo = _post(client, REINTENTAR, {"id": "no-existe"})

    assert cuerpo["errors"][0]["message"] == "Cobro no encontrado."


def test_payments_caido_da_error_publico_sin_5xx() -> None:
    with _cliente(PaymentsFalso(falla=True)) as client:
        cuerpo = _post(client, COBROS, {})

    assert cuerpo["errors"][0]["message"] == "No pudimos consultar los cobros. Intenta de nuevo."


def test_solo_el_backoffice_ve_y_reintenta_cobros() -> None:
    payments = PaymentsFalso([cobro_de_payments("c-1", "POL-1", "pendiente")])
    with _cliente(payments) as client:
        for rol in ("CLIENTE", "ASESOR"):
            assert _post(client, COBROS, {}, rol)["errors"][0]["message"] == (
                "No tienes permiso para esta operación."
            )
            assert _post(client, REINTENTAR, {"id": "c-1"}, rol)["errors"]

    assert payments.cobros["c-1"]["estado"] == "pendiente"  # nadie sin permiso lo tocó


def test_la_poliza_nunca_viaja_en_la_url_hacia_payments() -> None:
    """Regla #6: las URL quedan en los logs (httpx en el BFF, el acceso en PAYMENTS)."""
    vistas: list[tuple[str, bytes]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistas.append((str(request.url), request.content))
        return httpx.Response(200, json=[])

    async def correr() -> None:
        transporte = httpx.MockTransport(responder)
        async with httpx.AsyncClient(base_url="http://payments", transport=transporte) as cliente:
            await ClientePaymentsHttp(cliente).listar_cobros("POL-SECRETA-1", "pendiente")

    asyncio.run(correr())

    url, cuerpo = vistas[0]
    assert "POL-SECRETA-1" not in url
    assert b"POL-SECRETA-1" in cuerpo


def test_crear_cobro_desde_el_back_office() -> None:
    payments = PaymentsFalso()
    with _cliente(payments) as client:
        cobro = _post(client, CREAR, {"e": ENTRADA_COBRO})["data"]["crearCobro"]

    assert cobro["__typename"] == "Cobro"
    assert cobro["estado"] == "COBRADO"
    assert cobro["monto"] == {"valor": "80000.00", "moneda": "COP"}
    assert payments.ordenes[0]["id_intento_cobro"] == "intento-bo-1"


def test_reenviar_el_mismo_intento_devuelve_el_mismo_cobro() -> None:
    payments = PaymentsFalso()
    with _cliente(payments) as client:
        primero = _post(client, CREAR, {"e": ENTRADA_COBRO})["data"]["crearCobro"]
        segundo = _post(client, CREAR, {"e": ENTRADA_COBRO})["data"]["crearCobro"]

    assert primero["id"] == segundo["id"]
    assert len(payments.ordenes) == 1


def test_mismo_intento_con_otros_datos_da_error_cobro() -> None:
    otra = {**ENTRADA_COBRO, "monto": {"valor": "1.00", "moneda": "COP"}}
    with _cliente(PaymentsFalso()) as client:
        _post(client, CREAR, {"e": ENTRADA_COBRO})
        resultado = _post(client, CREAR, {"e": otra})["data"]["crearCobro"]

    assert resultado["__typename"] == "ErrorCobro"
    assert "otros datos" in resultado["mensaje"]


def test_crear_cobro_con_payments_caido_da_error_publico() -> None:
    with _cliente(PaymentsFalso(falla=True)) as client:
        cuerpo = _post(client, CREAR, {"e": ENTRADA_COBRO})

    assert cuerpo["errors"][0]["message"] == "No pudimos registrar el cobro. Intenta de nuevo."


def test_crear_cobro_no_loguea_token_ni_poliza(caplog) -> None:
    with caplog.at_level(logging.DEBUG), _cliente(PaymentsFalso()) as client:
        _post(client, CREAR, {"e": ENTRADA_COBRO})

    assert ENTRADA_COBRO["tokenMedioPago"] not in caplog.text
    assert ENTRADA_COBRO["polizaId"] not in caplog.text


def test_cliente_http_crear_cobro_traduce_409_y_422() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if b"conflicto" in request.content:
            return httpx.Response(409, json={"detail": "ya se usó con otros datos"})
        return httpx.Response(422, json={"detail": []})

    async def probar() -> tuple[str, str]:
        cliente = ClientePaymentsHttp(
            httpx.AsyncClient(base_url="http://payments", transport=httpx.MockTransport(handler))
        )
        mensajes = []
        for intento in ("conflicto", "vacio"):
            try:
                await cliente.crear_cobro({"id_intento_cobro": intento})
            except SolicitudInvalida as error:
                mensajes.append(str(error))
        return tuple(mensajes)

    conflicto, invalido = asyncio.run(probar())
    assert conflicto == "ya se usó con otros datos"
    assert invalido == "Los datos del cobro no son válidos."
