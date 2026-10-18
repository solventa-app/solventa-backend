"""Tokenización, estado de la tarjeta y latencia configurable (KAN-208)."""

import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ANIO = datetime.now(UTC).year + 3
VISA = {
    "numero": "4242 4242 4242 4242",
    "vencimiento_mes": 12,
    "vencimiento_anio": ANIO,
    "cvv": "123",
    "titular": "Ana Prueba",
}
MASTERCARD = {**VISA, "numero": "5555555555554444"}
AMEX = {**VISA, "numero": "378282246310005", "cvv": "1234"}


def _orden(token: str) -> dict:
    return {
        "referencia_comercio": f"ref-{uuid.uuid4()}",
        "poliza_id": "pol-1",
        "monto": "150000.00",
        "moneda": "COP",
        "token_medio_pago": token,
    }


def teardown_function() -> None:
    client.post("/control/modo", json={"modo": "sano"})
    client.delete("/control/latencia")
    for tarjeta in client.get("/control/tokens").json():
        client.post(f"/control/tokens/{tarjeta['token']}/estado", json={"estado": "activa"})


# ---------- tokenización


def test_tokeniza_una_visa_de_prueba() -> None:
    r = client.post("/v1/tokens", json=VISA)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["token"].startswith("tok_")
    assert (cuerpo["marca"], cuerpo["ultimos4"], cuerpo["estado"]) == ("visa", "4242", "activa")
    assert cuerpo["vencimiento_mes"] == 12 and cuerpo["vencimiento_anio"] == ANIO


def test_reconoce_mastercard_y_amex() -> None:
    assert client.post("/v1/tokens", json=MASTERCARD).json()["marca"] == "mastercard"
    amex = client.post("/v1/tokens", json=AMEX).json()
    assert (amex["marca"], amex["ultimos4"]) == ("amex", "0005")


def test_la_misma_tarjeta_da_el_mismo_token_y_otra_da_uno_distinto() -> None:
    primero = client.post("/v1/tokens", json=VISA).json()["token"]
    repetido = client.post("/v1/tokens", json={**VISA, "numero": "4242424242424242"}).json()[
        "token"
    ]
    otra = client.post("/v1/tokens", json=MASTERCARD).json()["token"]

    assert primero == repetido
    assert otra != primero


def test_nunca_devuelve_el_numero_ni_el_cvv() -> None:
    r = client.post("/v1/tokens", json=VISA)
    token = r.json()["token"]
    todo = r.text + client.get(f"/v1/tokens/{token}").text + client.get("/control/tokens").text

    assert "4242424242424242" not in todo and "4242 4242 4242 4242" not in todo
    assert '"123"' not in todo and "cvv" not in todo and "numero" not in todo


def test_los_errores_de_validacion_no_repiten_la_tarjeta() -> None:
    sin_cvv = {k: v for k, v in VISA.items() if k != "cvv"}
    r = client.post("/v1/tokens", json=sin_cvv)

    assert r.status_code == 422
    assert "4242" not in r.text and "input" not in r.text


def test_no_loguea_la_tarjeta(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        client.post("/v1/tokens", json=VISA)
        client.post("/v1/tokens", json={**VISA, "numero": "4242424242424241"})

    assert "4242424242424242" not in caplog.text and "4242424242424241" not in caplog.text


def test_rechaza_numero_con_luhn_invalido() -> None:
    r = client.post("/v1/tokens", json={**VISA, "numero": "4242424242424241"})

    assert r.status_code == 422 and r.json()["detail"] == "numero_invalido"


def test_rechaza_cvv_vencimiento_y_tarjeta_vencida() -> None:
    assert client.post("/v1/tokens", json={**VISA, "cvv": "12"}).json()["detail"] == "cvv_invalido"
    assert (
        client.post("/v1/tokens", json={**VISA, "vencimiento_mes": 13}).json()["detail"]
        == "vencimiento_invalido"
    )
    vencida = {**VISA, "vencimiento_anio": datetime.now(UTC).year - 1}
    assert client.post("/v1/tokens", json=vencida).json()["detail"] == "tarjeta_vencida"


def test_consultar_un_token_y_uno_desconocido() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]

    assert client.get(f"/v1/tokens/{token}").json()["ultimos4"] == "4242"
    assert client.get("/v1/tokens/tok_no_existe").status_code == 404


# ---------- estado de la tarjeta


def test_un_token_desconocido_se_sigue_cobrando() -> None:
    r = client.post("/v1/cobros", json=_orden("tok_demo_legado"))

    assert r.json()["estado"] == "cobrado"


def test_una_tarjeta_activa_cobra() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]

    assert client.post("/v1/cobros", json=_orden(token)).json()["estado"] == "cobrado"


def test_cada_estado_rechaza_con_su_motivo() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]
    for estado, motivo in [
        ("fondos_insuficientes", "fondos_insuficientes"),
        ("bloqueada", "tarjeta_bloqueada"),
        ("vencida", "tarjeta_vencida"),
    ]:
        assert (
            client.post(f"/control/tokens/{token}/estado", json={"estado": estado}).status_code
            == 200
        )
        cobro = client.post("/v1/cobros", json=_orden(token)).json()
        assert (cobro["estado"], cobro["motivo"], cobro["referencia"]) == (
            "rechazado",
            motivo,
            None,
        )


def test_volver_a_activa_permite_cobrar_de_nuevo() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]
    client.post(f"/control/tokens/{token}/estado", json={"estado": "bloqueada"})
    assert client.post("/v1/cobros", json=_orden(token)).json()["estado"] == "rechazado"

    client.post(f"/control/tokens/{token}/estado", json={"estado": "activa"})

    assert client.post("/v1/cobros", json=_orden(token)).json()["estado"] == "cobrado"


def test_el_estado_solo_afecta_a_esa_tarjeta() -> None:
    visa = client.post("/v1/tokens", json=VISA).json()["token"]
    master = client.post("/v1/tokens", json=MASTERCARD).json()["token"]
    client.post(f"/control/tokens/{visa}/estado", json={"estado": "fondos_insuficientes"})

    assert client.post("/v1/cobros", json=_orden(visa)).json()["estado"] == "rechazado"
    assert client.post("/v1/cobros", json=_orden(master)).json()["estado"] == "cobrado"


def test_retokenizar_no_reinicia_el_estado() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]
    client.post(f"/control/tokens/{token}/estado", json={"estado": "bloqueada"})

    assert client.post("/v1/tokens", json=VISA).json()["estado"] == "bloqueada"


def test_estado_de_un_token_desconocido_o_invalido() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]

    assert (
        client.post("/control/tokens/tok_no_existe/estado", json={"estado": "activa"}).status_code
        == 404
    )
    assert (
        client.post(f"/control/tokens/{token}/estado", json={"estado": "rara"}).status_code == 422
    )


def test_el_modo_error_gana_sobre_el_estado_de_la_tarjeta() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]
    client.post(f"/control/tokens/{token}/estado", json={"estado": "bloqueada"})
    client.post("/control/modo", json={"modo": "error"})

    assert client.post("/v1/cobros", json=_orden(token)).status_code == 503


def test_el_listado_de_control_muestra_el_estado() -> None:
    token = client.post("/v1/tokens", json=VISA).json()["token"]
    client.post(f"/control/tokens/{token}/estado", json={"estado": "vencida"})

    fila = next(t for t in client.get("/control/tokens").json() if t["token"] == token)
    assert (fila["marca"], fila["ultimos4"], fila["estado"]) == ("visa", "4242", "vencida")


# ---------- latencia configurable


def _tarda(llamada) -> float:
    inicio = time.perf_counter()
    llamada()
    return (time.perf_counter() - inicio) * 1000


def test_la_latencia_fijada_reemplaza_la_del_modo() -> None:
    r = client.post("/control/latencia", json={"minimo_ms": 400, "maximo_ms": 400})
    assert r.json() == {"minimo_ms": 400, "maximo_ms": 400}

    assert _tarda(lambda: client.get("/v1/estado")) >= 390
    assert client.get("/control/latencia").json() == {"minimo_ms": 400, "maximo_ms": 400}


def test_la_latencia_aplica_tambien_a_cobros_y_tokens() -> None:
    client.post("/control/latencia", json={"minimo_ms": 300, "maximo_ms": 300})

    assert _tarda(lambda: client.post("/v1/cobros", json=_orden("tok_x"))) >= 290
    assert _tarda(lambda: client.post("/v1/tokens", json=VISA)) >= 290


def test_la_latencia_alta_supera_el_timeout_del_acl_pero_el_cobro_queda_hecho() -> None:
    client.post("/control/latencia", json={"minimo_ms": 900, "maximo_ms": 900})
    orden = _orden("tok_lento")

    demora = _tarda(lambda: client.post("/v1/cobros", json=orden))

    # Supera los 700 ms con los que corta el ACL Worker: para él es una falla por timeout…
    assert demora >= 890
    # …pero la pasarela sí registró el cobro (como en la vida real): la conciliación lo ve.
    assert client.get(f"/v1/cobros/{orden['referencia_comercio']}").json()["estado"] == "cobrado"


def test_restaurar_vuelve_a_la_latencia_del_modo() -> None:
    client.post("/control/latencia", json={"minimo_ms": 600, "maximo_ms": 600})
    client.delete("/control/latencia")

    assert client.get("/control/latencia").json() == {"minimo_ms": None, "maximo_ms": None}
    assert _tarda(lambda: client.get("/v1/estado")) < 300


def test_la_latencia_rechaza_valores_invalidos() -> None:
    for cuerpo in [
        {"minimo_ms": -1, "maximo_ms": 10},
        {"minimo_ms": 500, "maximo_ms": 100},
        {"minimo_ms": 0, "maximo_ms": 999_999},
    ]:
        assert client.post("/control/latencia", json=cuerpo).status_code == 422
