"""Acceso biométrico del teléfono (KAN-26, contrato v0.44): registrar, retar y entrar con firma."""

from fakes import AuthFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

REGISTRAR = "mutation($k: String!) { registrarDispositivo(clavePublica: $k) }"
RETO = "mutation($d: ID!) { desafioDispositivo(dispositivoId: $d) { reto venceEn } }"
ENTRAR = """
mutation($d: ID!, $r: String!, $f: String!) {
  iniciarSesionConDispositivo(dispositivoId: $d, reto: $r, firma: $f) {
    __typename ... on Sesion { token refreshToken usuario { rol } }
    ... on ErrorAutenticacion { codigo mensaje }
  }
}
"""


def _pila():
    auth = AuthFalso()
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    cliente = TestClient(app)
    cliente.__enter__()
    return cliente, auth


def _gql(cliente, query, variables=None, canal="app", token=None):
    cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
    return cliente.post(
        f"/graphql/{canal}", json={"query": query, "variables": variables or {}}, headers=cabeceras
    ).json()


def _token_cliente(auth: AuthFalso) -> str:
    auth.usuarios["u-c"] = {**auth.usuarios["u-1"], "id": "u-c", "usuario": "ana", "rol": "CLIENTE"}
    return token_de_prueba("CLIENTE", usuario_id="u-c")


def test_flujo_completo_registrar_retar_y_entrar_sin_clave() -> None:
    cliente, auth = _pila()
    token = _token_cliente(auth)
    disp = _gql(cliente, REGISTRAR, {"k": "llave-1"}, token=token)["data"]["registrarDispositivo"]
    reto = _gql(cliente, RETO, {"d": disp})["data"]["desafioDispositivo"]["reto"]
    # Sin sesión: la persona cerró sesión y solo tiene la llave del teléfono.
    r = _gql(cliente, ENTRAR, {"d": disp, "r": reto, "f": f"firma:{reto}:llave-1"})
    sesion = r["data"]["iniciarSesionConDispositivo"]
    assert sesion["__typename"] == "Sesion" and sesion["refreshToken"]


def test_firma_mala_o_dispositivo_inexistente_dan_el_mismo_error() -> None:
    cliente, auth = _pila()
    token = _token_cliente(auth)
    disp = _gql(cliente, REGISTRAR, {"k": "llave-1"}, token=token)["data"]["registrarDispositivo"]
    reto = _gql(cliente, RETO, {"d": disp})["data"]["desafioDispositivo"]["reto"]
    mala = _gql(cliente, ENTRAR, {"d": disp, "r": reto, "f": "firma:otra"})
    nadie = _gql(cliente, ENTRAR, {"d": "no-existe", "r": "x", "f": "y"})
    assert mala["data"] == nadie["data"]
    assert mala["data"]["iniciarSesionConDispositivo"]["codigo"] == "DISPOSITIVO_NO_VALIDO"


def test_no_funciona_en_la_web_y_registrar_exige_sesion_de_la_app() -> None:
    cliente, auth = _pila()
    token = _token_cliente(auth)
    disp = _gql(cliente, REGISTRAR, {"k": "llave-1"}, token=token)["data"]["registrarDispositivo"]
    reto = _gql(cliente, RETO, {"d": disp})["data"]["desafioDispositivo"]["reto"]
    web = _gql(cliente, ENTRAR, {"d": disp, "r": reto, "f": f"firma:{reto}:llave-1"}, canal="web")
    assert web["data"]["iniciarSesionConDispositivo"]["codigo"] == "DISPOSITIVO_NO_VALIDO"
    sin_sesion = _gql(cliente, REGISTRAR, {"k": "llave-1"})
    assert sin_sesion.get("errors")
    desde_web = _gql(cliente, REGISTRAR, {"k": "llave-1"}, canal="web", token=token)
    assert desde_web.get("errors")


def test_una_llave_rechazada_por_auth_no_se_registra() -> None:
    cliente, auth = _pila()
    token = _token_cliente(auth)
    r = _gql(cliente, REGISTRAR, {"k": "basura"}, token=token)
    assert r.get("errors") and not auth.dispositivos
