"""KAN-259 en el BFF: sesión única por cliente en la app (D3) con `sids_revocados` anotado al
instante en `sesiones_revocadas` (D4), y las mutaciones `registrarTokenPush` y
`actualizarPreferenciaPush`. Con dobles (`AuthFalso`), sin AUTH real."""

from fakes import AuthFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

LOGIN_APP = """
mutation($dispositivo: DispositivoPushInput) {
  iniciarSesion(
    entrada: {usuario: "cliente.259", clave: "Clave-segura-1", dispositivo: $dispositivo}
  ) {
    __typename
    ... on Sesion { token usuario { notificacionesPush } }
  }
}
"""
YO = "query { yo { __typename ... on Usuario { id } } }"
REGISTRAR_TOKEN = """
mutation($i: String!, $t: String!) { registrarTokenPush(instalacionId: $i, tokenFcm: $t) }
"""
ACTUALIZAR_PREFERENCIA = "mutation($h: Boolean!) { actualizarPreferenciaPush(habilitado: $h) }"


def _cliente() -> tuple[TestClient, AuthFalso]:
    auth = AuthFalso()
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    return TestClient(app), auth


def _post(client: TestClient, query: str, variables: dict | None = None, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post(
        "/graphql/app", json={"query": query, "variables": variables or {}}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def _crear_cliente_app(client: TestClient, bo: str) -> None:
    crear = """mutation { crearUsuario(entrada: {usuario: "cliente.259", clave: "Clave-segura-1",
        rol: CLIENTE}) { ... on Usuario { id } } }"""
    _post(client, crear, token=bo)


def test_login_en_un_telefono_nuevo_revoca_al_instante_el_token_del_anterior() -> None:
    client, auth = _cliente()
    bo = token_de_prueba("BACKOFFICE", "u-bo")
    with client:
        _crear_cliente_app(client, bo)

        primera = _post(
            client,
            LOGIN_APP,
            {"dispositivo": {"instalacionId": "A", "plataforma": "ANDROID"}},
        )["data"]["iniciarSesion"]
        token_viejo = primera["token"]

        segunda = _post(
            client,
            LOGIN_APP,
            {"dispositivo": {"instalacionId": "B", "plataforma": "ANDROID"}},
        )["data"]["iniciarSesion"]
        assert segunda["__typename"] == "Sesion"

        # El token del teléfono A, aunque no venza en minutos, ya no sirve: el BFF lo anotó en
        # `sesiones_revocadas` desde el `sids_revocados` que devolvió AUTH (D3/D4).
        rechazado = _post(client, YO, token=token_viejo)
        assert rechazado["data"]["yo"] is None
        assert rechazado["errors"][0]["message"] != ""


def test_registrar_token_push_solo_sobre_el_dispositivo_activo() -> None:
    client, auth = _cliente()
    bo = token_de_prueba("BACKOFFICE", "u-bo")
    with client:
        _crear_cliente_app(client, bo)
        sesion = _post(
            client,
            LOGIN_APP,
            {"dispositivo": {"instalacionId": "A", "plataforma": "ANDROID"}},
        )["data"]["iniciarSesion"]
        token = sesion["token"]

        ok = _post(client, REGISTRAR_TOKEN, {"i": "A", "t": "token-fcm-real"}, token=token)
        assert ok["data"]["registrarTokenPush"] is True

        rechazado = _post(client, REGISTRAR_TOKEN, {"i": "otra-instalacion", "t": "x"}, token=token)
        assert rechazado["data"] is None and rechazado["errors"]


def test_actualizar_preferencia_push_apaga_las_notificaciones() -> None:
    client, auth = _cliente()
    bo = token_de_prueba("BACKOFFICE", "u-bo")
    with client:
        _crear_cliente_app(client, bo)
        sesion = _post(
            client,
            LOGIN_APP,
            {"dispositivo": {"instalacionId": "A", "plataforma": "ANDROID"}},
        )["data"]["iniciarSesion"]
        assert sesion["usuario"]["notificacionesPush"] is True
        token = sesion["token"]

        apagado = _post(client, ACTUALIZAR_PREFERENCIA, {"h": False}, token=token)
        assert apagado["data"]["actualizarPreferenciaPush"] is True

        otra_vez = _post(
            client,
            LOGIN_APP,
            {"dispositivo": {"instalacionId": "A", "plataforma": "ANDROID"}},
        )["data"]["iniciarSesion"]
        assert otra_vez["usuario"]["notificacionesPush"] is False
