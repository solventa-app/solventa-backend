"""Contrato v0.24 (perfil común): `Usuario.correo` solo para el propio usuario, en `iniciarSesion` y
`yo`. En listas, altas y cambios va en null y el teléfono sigue sin volver (EC022, W03-16)."""

from fakes import AuthFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

CORREO = "cliente@ejemplo.co"
TELEFONO = "+573001234567"


def _post(
    client: TestClient,
    query: str,
    variables: dict | None = None,
    rol: str = "BACKOFFICE",
    uid: str = "u-1",
):
    r = client.post(
        "/graphql/web",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, uid)}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _app(auth: AuthFalso) -> TestClient:
    return TestClient(
        create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    )


def _crear_cliente(client: TestClient) -> dict:
    entrada = {
        "usuario": "cliente.correo",
        "clave": "Clave-segura-1",
        "rol": "CLIENTE",
        "correo": CORREO,
        "telefono": TELEFONO,
    }
    consulta = (
        "mutation($e: CrearUsuarioInput!) "
        "{ crearUsuario(entrada: $e) { ... on Usuario { id correo } } }"
    )
    return _post(client, consulta, {"e": entrada})["data"]["crearUsuario"]


def test_correo_solo_en_login_y_yo_para_el_propio_usuario() -> None:
    auth = AuthFalso()
    with _app(auth) as client:
        creado = _crear_cliente(client)
        login = _post(
            client,
            "mutation { iniciarSesion(entrada: "
            '{usuario: "cliente.correo", clave: "Clave-segura-1"}) '
            "{ ... on Sesion { usuario { correo } } } }",
        )["data"]["iniciarSesion"]
        yo = _post(client, "{ yo { correo } }", rol="CLIENTE", uid=creado["id"])["data"]["yo"]
        lista = _post(client, "{ usuarios { correo } }")["data"]["usuarios"]

    assert creado["correo"] is None  # al crear no se devuelve
    assert login["usuario"]["correo"] == CORREO
    assert yo == {"correo": CORREO}
    assert all(u["correo"] is None for u in lista)


def test_sin_correo_es_null_y_el_telefono_no_existe_en_el_esquema() -> None:
    auth = AuthFalso()
    with _app(auth) as client:
        yo = _post(client, "{ yo { correo } }", rol="ASESOR", uid="no-tiene")
        telefono = _post(client, "{ yo { telefono } }")

    assert yo["data"]["yo"] is None or yo["data"]["yo"]["correo"] is None
    assert "telefono" in telefono["errors"][0]["message"]
