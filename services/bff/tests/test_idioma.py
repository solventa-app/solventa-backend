"""Mensajes en inglés cuando el frontend lo pide con `Accept-Language` (selector del header)."""

import pytest
from fakes import clientes_falsos
from fastapi.testclient import TestClient

from app import autenticacion_usuarios, esquema, monedas, servicio_oferta
from app.configuracion import Configuracion
from app.idioma import EN, idioma_de, traducir
from app.main import create_app


def _cliente(**fakes) -> TestClient:
    return TestClient(
        create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(**fakes))
    )


def _post(client: TestClient, query: str, idioma: str | None = None) -> dict:
    cabeceras = {"Accept-Language": idioma} if idioma else {}
    r = client.post("/graphql/web", json={"query": query}, headers=cabeceras)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    ("cabecera", "esperado"),
    [
        (None, "es"),
        ("", "es"),
        ("es", "es"),
        ("en", "en"),
        ("en-US,en;q=0.9", "en"),
        ("es-CO,es;q=0.9,en;q=0.8", "es"),
        ("es;q=0.5,en;q=0.9", "en"),
        ("fr", "es"),
        ("en;q=basura,es", "es"),
    ],
)
def test_idioma_de_la_cabecera(cabecera: str | None, esperado: str) -> None:
    assert idioma_de(cabecera) == esperado


def test_todos_los_mensajes_del_bff_tienen_traduccion() -> None:
    mensajes = [
        valor
        for modulo in (autenticacion_usuarios, esquema, monedas, servicio_oferta)
        for nombre, valor in vars(modulo).items()
        if nombre.startswith("MENSAJE_") and isinstance(valor, str)
    ]
    assert mensajes
    assert [m for m in mensajes if m not in EN] == []


def test_mensaje_con_numero_y_texto_desconocido() -> None:
    assert (
        traducir("Se agotaron los 3 reintentos: el cobro requiere otro medio de pago.", "en")
        == "All 3 retries were used: the payment needs another payment method."
    )
    assert traducir("Algo nuevo sin traducir.", "en") == "Algo nuevo sin traducir."
    assert traducir("Usuario o clave incorrectos.", "es") == "Usuario o clave incorrectos."


INICIAR_SESION = """
mutation { iniciarSesion(entrada: { usuario: "nadie", clave: "mala" }) {
  __typename ... on ErrorAutenticacion { mensaje } } }
"""


def test_error_de_datos_en_ingles_y_en_espanol() -> None:
    with _cliente() as client:
        en = _post(client, INICIAR_SESION, "en-US,en;q=0.9")["data"]["iniciarSesion"]
        es = _post(client, INICIAR_SESION)["data"]["iniciarSesion"]
    assert en["mensaje"] == "Incorrect username or password."
    assert es["mensaje"] == "Usuario o clave incorrectos."


def test_texto_de_la_respuesta_en_ingles() -> None:
    query = 'mutation { contactarAsesor(ofertaId: "o-1", canal: CHAT) { resumen } }'
    with _cliente() as client:
        cuerpo = _post(client, query, "en")
    assert cuerpo["data"]["contactarAsesor"]["resumen"] == (
        "We received your chat request. An advisor will contact you to continue your quote."
    )


def test_error_publico_en_ingles_y_el_inesperado_sin_tocar() -> None:
    with _cliente() as client:
        sin_sesion = _post(client, "{ cobros { id } }", "en")
    assert sin_sesion["errors"][0]["message"] == "You must sign in."
    assert traducir("Unexpected error.", "en") == "Unexpected error."
