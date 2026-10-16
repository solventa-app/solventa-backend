"""Cobros del back-office en el GraphQL del BFF (KAN-28, CA-W05-01/02), con dobles."""

import asyncio

import httpx
from fakes import PaymentsFalso, clientes_falsos, cobro_de_payments, token_de_prueba
from fastapi.testclient import TestClient

from app.clientes import ClientePaymentsHttp
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
