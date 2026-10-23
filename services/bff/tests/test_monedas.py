"""Preferencias de idioma y moneda, y conversión de montos para mostrar (KAN-209)."""

from decimal import Decimal

import pytest
from fakes import AclFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app
from app.monedas import ConversionInvalida, convertir

TASAS = {"COP": "1", "USD": "4000", "EUR": "4400", "CLP": "4"}


@pytest.mark.parametrize(
    ("valor", "origen", "destino", "esperado"),
    [
        ("61200.00", "COP", "USD", "15.30"),
        ("61200.00", "COP", "COP", "61200.00"),
        ("10", "USD", "EUR", "9.09"),
        ("10.00", "USD", "CLP", "10000"),
        ("200000000", "COP", "EUR", "45454.55"),
    ],
)
def test_convierte_con_decimal_y_decimales_iso(valor, origen, destino, esperado) -> None:
    assert convertir(valor, origen, destino, TASAS) == esperado
    assert Decimal(esperado) == Decimal(convertir(valor, origen, destino, TASAS))


@pytest.mark.parametrize(("valor", "destino"), [("10", "JPY"), ("abc", "USD"), ("NaN", "USD")])
def test_rechaza_moneda_o_monto_invalido(valor, destino) -> None:
    with pytest.raises(ConversionInvalida):
        convertir(valor, "COP", destino, TASAS | {"JPY": "27"})


def _cliente(**fakes) -> TestClient:
    return TestClient(
        create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(**fakes))
    )


def _post(client, query, variables=None, rol="CLIENTE", usuario_id="u-1", idioma=None):
    cabeceras = {"Authorization": f"Bearer {token_de_prueba(rol, usuario_id)}"}
    if idioma:
        cabeceras["Accept-Language"] = idioma
    r = client.post(
        "/graphql/web", json={"query": query, "variables": variables or {}}, headers=cabeceras
    )
    assert r.status_code == 200, r.text
    return r.json()


CONVERTIR = """
query($m: [MontoInput!]!, $moneda: String!) { convertirMontos(montos: $m, moneda: $moneda) {
  valor moneda } }
"""


def test_convertir_montos_por_graphql() -> None:
    montos = [{"valor": "61200.00", "moneda": "COP"}, {"valor": "20000000", "moneda": "COP"}]
    with _cliente() as client:
        cuerpo = _post(client, CONVERTIR, {"m": montos, "moneda": "USD"})
    assert cuerpo["data"]["convertirMontos"] == [
        {"valor": "15.30", "moneda": "USD"},
        {"valor": "5000.00", "moneda": "USD"},
    ]


def test_convertir_montos_degrada_con_mensaje_y_exige_sesion() -> None:
    montos = [{"valor": "1", "moneda": "COP"}]
    with _cliente(acl=AclFalso(fallan={"tasas"})) as client:
        caido = _post(client, CONVERTIR, {"m": montos, "moneda": "USD"}, idioma="en")
    with _cliente() as client:
        no_soportada = _post(client, CONVERTIR, {"m": montos, "moneda": "JPY"})
        sin_sesion = client.post(
            "/graphql/web", json={"query": CONVERTIR, "variables": {"m": montos, "moneda": "USD"}}
        ).json()
    assert caido["errors"][0]["message"] == "We couldn't convert the amounts right now."
    assert no_soportada["errors"][0]["message"] == "Moneda no soportada."
    assert sin_sesion["errors"][0]["message"] == "Debes iniciar sesión."


PREFERENCIAS = """
mutation($e: PreferenciasInput!) { actualizarPreferencias(entrada: $e) {
  __typename ... on Usuario { idioma moneda } ... on ErrorUsuario { mensaje } } }
"""


def test_actualizar_preferencias_y_leerlas_en_yo() -> None:
    with _cliente() as client:
        cambio = _post(client, PREFERENCIAS, {"e": {"idioma": "EN", "moneda": "USD"}}, rol="ASESOR")
        yo = _post(client, "{ yo { idioma moneda } }", rol="ASESOR")
        invalido = _post(
            client, PREFERENCIAS, {"e": {"idioma": "FR", "moneda": "USD"}}, rol="ASESOR"
        )
    assert cambio["data"]["actualizarPreferencias"] == {
        "__typename": "Usuario",
        "idioma": "EN",
        "moneda": "USD",
    }
    assert yo["data"]["yo"] == {"idioma": "EN", "moneda": "USD"}
    assert invalido["data"]["actualizarPreferencias"]["__typename"] == "ErrorUsuario"
