"""Contacto del CLIENTE (HU-W03, bloque 8, W03-17): `crearUsuario` y `actualizarContacto`.

El BFF solo lo escribe y lo reenvía a AUTH: no lo guarda, no lo registra y nunca lo devuelve
(`Usuario.tieneContacto` dice si hay, no cuál)."""

import logging

import pytest
from fakes import AuthFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

CORREO = "maria.gomez@ejemplo.co"
TELEFONO = "+573001234567"

CREAR = """mutation($e: CrearUsuarioInput!) {
  crearUsuario(entrada: $e) {
    __typename
    ... on Usuario { id rol tieneContacto }
    ... on ErrorUsuario { mensaje }
  }
}"""
CONTACTO = """mutation($e: ContactoInput!) {
  actualizarContacto(entrada: $e) {
    __typename
    ... on Usuario { id tieneContacto }
    ... on ErrorUsuario { mensaje }
  }
}"""
YO = "{ yo { tieneContacto } }"


def _cliente(auth: AuthFalso) -> TestClient:
    return TestClient(
        create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    )


def _post(client, query, variables=None, rol="BACKOFFICE", uid="u-bo", idioma=None):
    cabeceras = {"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"}
    if idioma:
        cabeceras["Accept-Language"] = idioma
    r = client.post(
        "/graphql/web", json={"query": query, "variables": variables or {}}, headers=cabeceras
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _nuevo_cliente(client, **contacto) -> str:
    entrada = {"usuario": "cliente.uno", "clave": "Clave-segura-1", "rol": "CLIENTE", **contacto}
    return _post(client, CREAR, {"e": entrada})["data"]["crearUsuario"]["id"]


# ---------- crearUsuario con contacto


def test_crear_un_cliente_con_contacto_se_lo_entrega_a_auth_y_no_lo_devuelve() -> None:
    auth = AuthFalso()
    with _cliente(auth) as client:
        cuerpo = _post(
            client,
            CREAR,
            {
                "e": {
                    "usuario": "cliente.uno",
                    "clave": "Clave-segura-1",
                    "rol": "CLIENTE",
                    "correo": CORREO,
                    "telefono": TELEFONO,
                }
            },
        )

    creado = cuerpo["data"]["crearUsuario"]
    assert creado["__typename"] == "Usuario" and creado["tieneContacto"] is True
    assert auth.contactos[creado["id"]] == {"correo": CORREO, "telefono": TELEFONO}
    assert CORREO not in str(cuerpo) and "3001234567" not in str(cuerpo)


def test_crear_un_cliente_sin_contacto_dice_que_no_lo_tiene() -> None:
    with _cliente(AuthFalso()) as client:
        cuerpo = _post(
            client,
            CREAR,
            {"e": {"usuario": "cliente.uno", "clave": "Clave-segura-1", "rol": "CLIENTE"}},
        )

    assert cuerpo["data"]["crearUsuario"]["tieneContacto"] is False


def test_un_asesor_con_contacto_se_rechaza_con_el_mensaje_de_auth() -> None:
    with _cliente(AuthFalso()) as client:
        cuerpo = _post(
            client,
            CREAR,
            {
                "e": {
                    "usuario": "asesor.dos",
                    "clave": "Clave-segura-1",
                    "rol": "ASESOR",
                    "correo": CORREO,
                }
            },
        )

    error = cuerpo["data"]["crearUsuario"]
    assert error == {
        "__typename": "ErrorUsuario",
        "mensaje": "El contacto solo se guarda para clientes.",
    }


# ---------- actualizarContacto


def test_el_cliente_actualiza_su_contacto_y_auth_recibe_el_id_del_token() -> None:
    auth = AuthFalso()
    with _cliente(auth) as client:
        uid = _nuevo_cliente(client)
        cuerpo = _post(client, CONTACTO, {"e": {"correo": CORREO}}, rol="CLIENTE", uid=uid)
        propio = _post(client, YO, rol="CLIENTE", uid=uid)

    assert cuerpo["data"]["actualizarContacto"] == {
        "__typename": "Usuario",
        "id": uid,
        "tieneContacto": True,
    }
    assert auth.contactos[uid] == {"correo": CORREO, "telefono": None}
    assert propio["data"]["yo"]["tieneContacto"] is True
    assert CORREO not in str(cuerpo)


def test_lo_que_no_llega_se_conserva() -> None:
    auth = AuthFalso()
    with _cliente(auth) as client:
        uid = _nuevo_cliente(client, correo=CORREO)
        _post(client, CONTACTO, {"e": {"telefono": TELEFONO}}, rol="CLIENTE", uid=uid)

    assert auth.contactos[uid] == {"correo": CORREO, "telefono": TELEFONO}


@pytest.mark.parametrize(
    ("entrada", "mensaje"),
    [
        ({}, "Indica un correo o un teléfono."),
        ({"correo": "no-es-un-correo"}, "El correo no es válido."),
        (
            {"telefono": "abc"},
            "El teléfono no es válido: usa entre 7 y 15 dígitos, con un + opcional al inicio.",
        ),
    ],
)
def test_un_contacto_invalido_es_un_error_de_usuario_no_un_5xx(entrada: dict, mensaje: str) -> None:
    with _cliente(AuthFalso()) as client:
        uid = _nuevo_cliente(client)
        cuerpo = _post(client, CONTACTO, {"e": entrada}, rol="CLIENTE", uid=uid)

    assert cuerpo["data"]["actualizarContacto"] == {
        "__typename": "ErrorUsuario",
        "mensaje": mensaje,
    }


def test_los_errores_de_contacto_salen_en_ingles_si_el_usuario_lo_pide() -> None:
    with _cliente(AuthFalso()) as client:
        uid = _nuevo_cliente(client)
        cuerpo = _post(
            client, CONTACTO, {"e": {"correo": "malo"}}, rol="CLIENTE", uid=uid, idioma="en"
        )

    assert cuerpo["data"]["actualizarContacto"]["mensaje"] == "The email address is not valid."


def test_un_usuario_que_auth_no_conoce_es_un_error_de_usuario() -> None:
    with _cliente(AuthFalso()) as client:
        cuerpo = _post(client, CONTACTO, {"e": {"correo": CORREO}}, rol="CLIENTE", uid="no-existe")

    assert cuerpo["data"]["actualizarContacto"] == {
        "__typename": "ErrorUsuario",
        "mensaje": "Usuario no encontrado.",
    }


def test_si_auth_no_responde_es_un_error_con_mensaje_no_un_5xx() -> None:
    with _cliente(AuthFalso(falla=True)) as client:
        cuerpo = _post(client, CONTACTO, {"e": {"correo": CORREO}}, rol="CLIENTE", uid="u-9")

    assert cuerpo["data"] is None
    assert cuerpo["errors"][0]["message"] == "No pudimos procesar la solicitud. Intenta de nuevo."


# ---------- permisos y privacidad


@pytest.mark.parametrize(("rol", "uid"), [("ASESOR", "u-1"), ("BACKOFFICE", "u-bo")])
def test_solo_el_cliente_actualiza_su_contacto(rol: str, uid: str) -> None:
    auth = AuthFalso()
    with _cliente(auth) as client:
        cuerpo = _post(client, CONTACTO, {"e": {"correo": CORREO}}, rol=rol, uid=uid)

    assert cuerpo["data"] is None
    assert cuerpo["errors"][0]["message"] == "No tienes permiso para esta operación."
    assert auth.contactos == {}


def test_sin_token_exige_sesion() -> None:
    with _cliente(AuthFalso()) as client:
        r = client.post(
            "/graphql/web", json={"query": CONTACTO, "variables": {"e": {"correo": CORREO}}}
        )

    assert r.json()["errors"][0]["message"] == "Debes iniciar sesión."


@pytest.mark.parametrize("campo", ["telefono", "correoCifrado", "contacto"])
def test_el_contacto_no_se_puede_leer_por_graphql(campo: str) -> None:
    with _cliente(AuthFalso()) as client:
        uid = _nuevo_cliente(client, correo=CORREO)
        cuerpo = _post(client, f"{{ yo {{ {campo} }} }}", rol="CLIENTE", uid=uid)

    assert cuerpo.get("data") is None
    assert "Cannot query field" in cuerpo["errors"][0]["message"]
    assert CORREO not in str(cuerpo)


def test_el_correo_solo_vuelve_en_yo_y_el_telefono_nunca() -> None:
    """Contrato v0.24: `Usuario.correo` es del propio usuario; el teléfono no vuelve."""
    with _cliente(AuthFalso()) as client:
        uid = _nuevo_cliente(client, correo=CORREO, telefono=TELEFONO)
        cuerpo = _post(client, "{ yo { correo } }", rol="CLIENTE", uid=uid)

    assert cuerpo["data"]["yo"] == {"correo": CORREO}
    assert TELEFONO not in str(cuerpo)


def test_los_logs_no_llevan_el_contacto(caplog) -> None:
    with caplog.at_level(logging.DEBUG), _cliente(AuthFalso()) as client:
        uid = _nuevo_cliente(client, correo=CORREO, telefono=TELEFONO)
        _post(client, CONTACTO, {"e": {"correo": "otro@ejemplo.co"}}, rol="CLIENTE", uid=uid)
        _post(client, CONTACTO, {"e": {"correo": "esto-no-es-un-correo"}}, rol="CLIENTE", uid=uid)

    for dato in (CORREO, "maria.gomez", "otro@ejemplo.co", "3001234567", "esto-no-es-un-correo"):
        assert dato not in caplog.text
