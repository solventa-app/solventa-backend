"""`ClienteUnder` y `ClientePayments` contra respuestas HTTP simuladas (sin los servicios)."""

import json

import httpx
import pytest

from app.application.cliente_payments import (
    ClientePayments,
    CobroEnConflicto,
    OrdenPrimerCobro,
    PaymentsNoDisponible,
)
from app.application.cliente_under import ClienteUnder, UnderNoDisponible

COBERTURA = {"valor": "200000000", "moneda": "COP"}
ORDEN = OrdenPrimerCobro("intento-1", "p-1", "usuario-1", "185000", "COP", "tok_recaudo")


def _http(clase, responder, vistas: list[dict] | None = None):
    def manejar(peticion: httpx.Request) -> httpx.Response:
        if vistas is not None:
            vistas.append(json.loads(peticion.content))
        return responder()

    cliente = clase("http://destino", 1.0)
    cliente._cliente = httpx.Client(
        base_url="http://destino", transport=httpx.MockTransport(manejar)
    )
    return cliente


def test_under_manda_el_riesgo_la_cobertura_y_el_cliente_opaco() -> None:
    vistas: list[dict] = []
    cuerpo = {"decision": "REVISION_MANUAL", "motivos": ["RIESGO_ALTO"], "kyc": {}}

    resultado = _http(ClienteUnder, lambda: httpx.Response(200, json=cuerpo), vistas).decidir(
        "p-1", "cliente-opaco", "ALTO", COBERTURA
    )

    assert (resultado.decision, resultado.motivos) == ("REVISION_MANUAL", ["RIESGO_ALTO"])
    assert vistas == [
        {
            "propuesta_id": "p-1",
            "cliente_id": "cliente-opaco",
            "nivel_riesgo": "ALTO",
            "cobertura": COBERTURA,
        }
    ]


@pytest.mark.parametrize(
    "respuesta",
    [
        lambda: httpx.Response(503),
        lambda: httpx.Response(200, content=b"no json"),
        lambda: httpx.Response(200, json={}),
        lambda: httpx.Response(200, json=[]),
    ],
)
def test_under_que_no_responde_o_responde_mal_es_no_disponible(respuesta) -> None:
    with pytest.raises(UnderNoDisponible):
        _http(ClienteUnder, respuesta).decidir("p-1", "c", "BAJO", COBERTURA)


def test_payments_recibe_la_orden_sin_datos_de_tarjeta() -> None:
    vistas: list[dict] = []
    cuerpo = {"id": "cobro-1", "estado": "cobrado", "motivo": None}

    resultado = _http(
        ClientePayments, lambda: httpx.Response(200, json=cuerpo), vistas
    ).crear_cobro(ORDEN)

    assert (resultado.estado, resultado.cobro_id) == ("cobrado", "cobro-1")
    assert vistas == [
        {
            "id_intento_cobro": "intento-1",
            "poliza_id": "p-1",
            "cliente_id": "usuario-1",
            "monto": "185000",
            "moneda": "COP",
            "token_medio_pago": "tok_recaudo",
        }
    ]


def test_payments_409_es_un_conflicto_de_cobro_no_un_error_de_servicio() -> None:
    with pytest.raises(CobroEnConflicto):
        _http(
            ClientePayments, lambda: httpx.Response(409, json={"detail": "otra orden"})
        ).crear_cobro(ORDEN)


@pytest.mark.parametrize(
    "respuesta",
    [
        lambda: httpx.Response(500),
        lambda: httpx.Response(422, json={"detail": []}),
        lambda: httpx.Response(200, content=b"no json"),
        lambda: httpx.Response(200, json={}),
    ],
)
def test_payments_que_no_responde_o_responde_mal_es_no_disponible(respuesta) -> None:
    with pytest.raises(PaymentsNoDisponible):
        _http(ClientePayments, respuesta).crear_cobro(ORDEN)
