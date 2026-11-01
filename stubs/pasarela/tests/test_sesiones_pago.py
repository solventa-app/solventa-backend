"""Sesiones de pago y página alojada del stub (W03-28): cada tarjeta de prueba, idempotencia,
vencimiento, lista blanca del retorno, modos de falla, API alterna y ninguna tarjeta en los logs."""

import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import tarjetas
from app.main import create_app

AHORA = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
RETORNO = "http://localhost:5173/pago/retorno?propuesta=p-1"
TARJETA_OK = {
    "tipo": "CREDITO",
    "numero": "4242 4242 4242 4242",
    "nombre": "Ana Prueba",
    "vencimiento": "12/30",
    "cvv": "123",
}


class Reloj:
    def __init__(self) -> None:
        self.ahora = AHORA

    def __call__(self) -> datetime:
        return self.ahora


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def client(reloj: Reloj) -> TestClient:
    return TestClient(create_app(reloj=reloj))


def _sesion(client: TestClient, referencia: str = "int-1", **cambios) -> dict:
    cuerpo = {
        "referencia_comercio": referencia,
        "monto": "185000",
        "moneda": "COP",
        "descripcion": "Seguro de desempleo",
        "url_retorno": RETORNO,
        **cambios,
    }
    r = client.post("/v1/sesiones-pago", json=cuerpo)
    assert r.status_code == 201, r.text
    return r.json()


def _pagar(client: TestClient, sesion_id: str, **cambios):
    return client.post(f"/checkout/{sesion_id}/pagar", json={**TARJETA_OK, **cambios})


# ---------- tarjetas ----------


@pytest.mark.parametrize(
    ("numero", "valido"),
    [
        ("4242424242424242", True),
        ("5555555555554444", True),
        ("4000000000000002", True),
        ("4242424242424241", False),
        ("1234", False),
        ("4242a24242424242", False),
    ],
)
def test_luhn(numero: str, valido: bool) -> None:
    assert tarjetas.luhn_valido(numero) is valido


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("numero", "4242 4242 4242 4241"),
        ("nombre", " "),
        ("vencimiento", "09/26"),  # ya pasó (hoy es octubre de 2026)
        ("vencimiento", "13/30"),
        ("cvv", "12"),
        ("tipo", "PREPAGO"),
    ],
)
def test_validar_dice_que_campo_corregir(campo: str, valor: str) -> None:
    with pytest.raises(tarjetas.TarjetaInvalida) as error:
        tarjetas.validar({**TARJETA_OK, campo: valor}, date(2026, 10, 27))
    assert error.value.campo == campo


def test_vence_el_ultimo_dia_del_mes_impreso() -> None:
    assert tarjetas.validar({**TARJETA_OK, "vencimiento": "10/26"}, date(2026, 10, 27))


# ---------- sesiones ----------


def test_crear_sesion_devuelve_la_url_de_la_pagina_y_vence_a_los_10_min(client) -> None:
    sesion = _sesion(client)
    assert sesion["estado"] == "ABIERTA"
    assert sesion["url_pago"] == f"http://localhost:8103/checkout/{sesion['sesion_id']}"
    assert datetime.fromisoformat(sesion["vence_en"]) == AHORA + timedelta(minutes=10)
    assert sesion["medio"] is None


def test_la_misma_referencia_devuelve_la_misma_sesion(client) -> None:
    assert _sesion(client)["sesion_id"] == _sesion(client)["sesion_id"]
    assert _sesion(client, "int-2")["sesion_id"] != _sesion(client)["sesion_id"]


@pytest.mark.parametrize(
    "url",
    ["https://atacante.example/robar", "javascript:alert(1)", "http://localhost:9999/x"],
)
def test_url_de_retorno_fuera_de_la_lista_blanca(client, url: str) -> None:
    r = client.post(
        "/v1/sesiones-pago",
        json={
            "referencia_comercio": "int-x",
            "monto": "1",
            "moneda": "COP",
            "descripcion": "x",
            "url_retorno": url,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "url_retorno_no_permitida"


def test_sesion_vencida_no_se_paga_ni_se_reabre(client, reloj) -> None:
    sesion = _sesion(client)
    reloj.ahora = AHORA + timedelta(minutes=10)
    assert client.get(f"/v1/sesiones-pago/{sesion['sesion_id']}").json()["estado"] == "VENCIDA"
    r = _pagar(client, sesion["sesion_id"])
    assert r.status_code == 409 and r.json()["estado"] == "VENCIDA"
    pagina = client.get(f"/checkout/{sesion['sesion_id']}")
    assert "La sesión venció" in pagina.text and "Regresar al comercio" in pagina.text
    # La misma referencia sigue siendo la sesión vencida: el comercio pide otra con otra referencia.
    assert _sesion(client)["estado"] == "VENCIDA"


def test_sesion_inexistente(client) -> None:
    assert client.get("/v1/sesiones-pago/ses_nada").status_code == 404
    assert client.get("/checkout/ses_nada").status_code == 404


# ---------- página y pago ----------


def test_la_pagina_lleva_csp_estricta_el_resumen_y_el_regreso(client) -> None:
    sesion = _sesion(client)
    r = client.get(f"/checkout/{sesion['sesion_id']}")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "Solventa" in r.text and "COP 185.000" in r.text
    regreso = f"{RETORNO}&amp;sesion={sesion['sesion_id']}"
    assert regreso in r.text
    assert "http" not in r.text.split("<script")[1].split(">")[0].replace('src="/', "")  # sin CDN
    assert client.get("/checkout-estaticos/checkout.js").status_code == 200


def test_la_pagina_en_ingles(client) -> None:
    sesion = _sesion(client, idioma="en")
    texto = client.get(f"/checkout/{sesion['sesion_id']}").text
    assert 'lang="en"' in texto and "Return to merchant" in texto


@pytest.mark.parametrize(
    ("numero", "tipo", "estado", "motivo", "marca"),
    [
        ("4242424242424242", "CREDITO", "COBRADA", None, "VISA"),
        ("5555555555554444", "DEBITO", "COBRADA", None, "MASTERCARD"),
        ("4000000000000002", "CREDITO", "RECHAZADA", "fondos_insuficientes", None),
        ("4000000000000069", "CREDITO", "RECHAZADA", "tarjeta_vencida", None),
        ("4000000000000119", "CREDITO", "FALLIDA", "error_procesamiento", None),
    ],
)
def test_cada_tarjeta_de_prueba(client, numero, tipo, estado, motivo, marca) -> None:
    sesion = _sesion(client)
    r = _pagar(client, sesion["sesion_id"], numero=numero, tipo=tipo)
    assert r.status_code == 200
    assert r.json() == {"estado": estado, "motivo": motivo}

    consultada = client.get(f"/v1/sesiones-pago/{sesion['sesion_id']}").json()
    assert consultada["estado"] == estado
    if estado == "COBRADA":
        assert consultada["medio"]["token"].startswith("tok_")
        assert consultada["medio"]["marca"] == marca
        assert consultada["medio"]["tipo"] == tipo
        assert consultada["referencia"].startswith("pg-")
        # Ni el número ni los últimos dígitos salen hacia el comercio.
        assert numero[-4:] not in str(consultada)
        # La conciliación por referencia ve el mismo cobro.
        cobro = client.get("/v1/cobros/int-1").json()
        assert cobro["estado"] == "cobrado" and cobro["referencia"] == consultada["referencia"]
    else:
        assert consultada["medio"] is None


def test_una_sesion_se_paga_una_sola_vez(client) -> None:
    sesion = _sesion(client)
    assert _pagar(client, sesion["sesion_id"]).json()["estado"] == "COBRADA"
    repetido = _pagar(client, sesion["sesion_id"])
    assert repetido.status_code == 409 and repetido.json()["estado"] == "COBRADA"


def test_tarjeta_invalida_deja_la_sesion_abierta_y_no_repite_la_tarjeta(client) -> None:
    sesion = _sesion(client)
    r = _pagar(client, sesion["sesion_id"], numero="4242424242424241")
    assert r.status_code == 422
    assert r.json() == {"campo": "numero"}
    assert client.get(f"/v1/sesiones-pago/{sesion['sesion_id']}").json()["estado"] == "ABIERTA"


def test_cuerpo_que_no_es_json(client) -> None:
    sesion = _sesion(client)
    r = client.post(f"/checkout/{sesion['sesion_id']}/pagar", content=b"no-json")
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("modo", "estado", "motivo"),
    [
        ("rechaza", "RECHAZADA", "fondos_insuficientes"),
        ("error", "FALLIDA", "pasarela_no_disponible"),
    ],
)
def test_modos_al_pagar(client, modo, estado, motivo) -> None:
    sesion = _sesion(client)
    r = client.post(f"/checkout/{sesion['sesion_id']}/pagar?modo={modo}", json=TARJETA_OK)
    assert r.json() == {"estado": estado, "motivo": motivo}


def test_modo_error_al_crear_y_al_consultar(client) -> None:
    sesion = _sesion(client)
    r = client.post(
        "/v1/sesiones-pago?modo=error",
        json={
            "referencia_comercio": "int-err",
            "monto": "1",
            "moneda": "COP",
            "descripcion": "x",
            "url_retorno": RETORNO,
        },
    )
    assert r.status_code == 503
    consulta = client.get(
        f"/v1/sesiones-pago/{sesion['sesion_id']}", headers={"X-Modo-Simulado": "error"}
    )
    assert consulta.status_code == 503


def test_ningun_dato_de_tarjeta_en_los_logs(client, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    sesion = _sesion(client)
    _pagar(client, sesion["sesion_id"], numero="4242424242424241")  # inválida
    _pagar(client, sesion["sesion_id"])  # aprobada
    registro = caplog.text
    for dato in (
        "4242424242424242",
        "4242 4242 4242 4242",
        "4242424242424241",
        "Ana Prueba",
        "12/30",
    ):
        assert dato not in registro
    assert "cvv" not in registro.lower()


# ---------- proveedor alterno (EC031) ----------


def test_api_alterna_de_sesiones(client) -> None:
    r = client.post(
        "/alterna/v2/checkout-sessions",
        json={
            "reference": "int-alt",
            "amount": "185000",
            "currency": "COP",
            "description": "Seguro",
            "return_url": RETORNO,
        },
    )
    assert r.status_code == 201
    creada = r.json()
    assert creada["status"] == "OPEN" and creada["checkout_url"].endswith(creada["id"])

    _pagar(client, creada["id"], numero="5555555555554444", tipo="DEBITO")
    consultada = client.get(f"/alterna/v2/checkout-sessions/{creada['id']}").json()
    assert consultada["status"] == "SUCCEEDED"
    assert consultada["payment_method"]["brand"] == "MASTERCARD"
    assert consultada["payment_method"]["funding"] == "debit"
    assert client.get("/alterna/v2/checkout-sessions/nada").status_code == 404


# ---------- cobros con el token (W05-A7) ----------


def _token_de(client: TestClient, numero: str, referencia: str = "int-1") -> str:
    sesion = _sesion(client, referencia)
    assert _pagar(client, sesion["sesion_id"], numero=numero).json()["estado"] == "COBRADA"
    return client.get(f"/v1/sesiones-pago/{sesion['sesion_id']}").json()["medio"]["token"]


def _cobrar(client: TestClient, referencia: str, token: str) -> dict:
    cuerpo = {
        "referencia_comercio": referencia,
        "poliza_id": "p-1",
        "monto": "185000",
        "moneda": "COP",
        "token_medio_pago": token,
    }
    return client.post("/v1/cobros", json=cuerpo).json()


def test_la_tarjeta_de_rechazo_recurrente_paga_en_linea_y_falla_con_su_token(client) -> None:
    token = _token_de(client, "4000000000000341")

    cobro = _cobrar(client, "rec-1", token)

    assert (cobro["estado"], cobro["motivo"]) == ("rechazado", "fondos_insuficientes")


def test_una_tarjeta_normal_sigue_cobrando_con_su_token(client) -> None:
    assert _cobrar(client, "rec-1", _token_de(client, "4242424242424242"))["estado"] == "cobrado"


def test_un_token_desconocido_sigue_la_regla_del_modo(client) -> None:
    assert _cobrar(client, "rec-1", "tok_convenio")["estado"] == "cobrado"


def test_la_api_alterna_tambien_rechaza_por_el_token(client) -> None:
    cuerpo = {
        "reference": "rec-1",
        "policy": "p-1",
        "amount": "185000",
        "currency": "COP",
        "payment_token": _token_de(client, "4000000000000341"),
    }

    assert client.post("/alterna/v2/charges", json=cuerpo).json()["status"] != "succeeded"
