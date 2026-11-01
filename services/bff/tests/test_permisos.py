"""Autenticación y autorización por rol en el GraphQL del BFF (HU-M02, CA-W01-11)."""

import logging
from dataclasses import replace

import pytest
from fakes import AuthFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import (
    MENSAJE_SIN_PERMISO,
    MENSAJE_SIN_SESION,
    MENSAJE_TOKEN_INVALIDO,
)
from app.configuracion import Configuracion
from app.main import create_app

ENTRADA_OFERTA = {
    "consentimientoId": "c-1",
    "documentoIdentidad": "CC-1020304050",
    "valorCredito": "200000000",
    "plazoMeses": 60,
    "direccionInmueble": "Calle 1 # 2-3",
}

OPERACIONES = {
    "solicitarOferta": (
        "mutation($e: SolicitudOfertaInput!) { solicitarOferta(entrada: $e) { __typename } }",
        {"e": ENTRADA_OFERTA},
    ),
    "registrarConsentimiento": (
        'mutation { registrarConsentimiento(entrada: {finalidad: "f", versionTexto: "v1"}) '
        "{ id } }",
        {},
    ),
    "contactarAsesor": ('mutation { contactarAsesor(ofertaId: "o-1", canal: CHAT) { canal } }', {}),
    "cobros": ("{ cobros { id } }", {}),
    "usuarios": ("{ usuarios { id } }", {}),
    "crearCobro": (
        'mutation { crearCobro(entrada: {idIntentoCobro: "i-1", polizaId: "P-1", '
        'monto: {valor: "100.00", moneda: "COP"}, tokenMedioPago: "tok_1"}) { __typename } }',
        {},
    ),
    "crearUsuario": (
        'mutation { crearUsuario(entrada: {usuario: "nuevo.uno", clave: "Clave-segura-1", '
        "rol: CLIENTE}) { __typename } }",
        {},
    ),
}


def _cliente(modo: str = "opcional", auth: AuthFalso | None = None) -> TestClient:
    config = replace(Configuracion.desde_entorno(), autenticacion_usuarios=modo)
    return TestClient(create_app(config=config, clientes=clientes_falsos(auth=auth)))


def _post(client: TestClient, query: str, variables: dict | None = None, token: str | None = None):
    cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post(
        "/graphql/web", json={"query": query, "variables": variables or {}}, headers=cabeceras
    )
    assert r.status_code == 200, r.text  # nunca un 5xx, ni siquiera sin permiso
    return r.json()


def _error(cuerpo: dict) -> str | None:
    return cuerpo["errors"][0]["message"] if cuerpo.get("errors") else None


# (operación, rol) -> ¿permitido? Es la matriz de docs/autenticacion-usuarios.md.
MATRIZ = {
    ("solicitarOferta", "CLIENTE"): True,
    ("solicitarOferta", "ASESOR"): True,
    ("solicitarOferta", "BACKOFFICE"): False,
    ("registrarConsentimiento", "CLIENTE"): True,
    ("registrarConsentimiento", "ASESOR"): True,
    ("registrarConsentimiento", "BACKOFFICE"): False,
    ("contactarAsesor", "CLIENTE"): True,
    ("contactarAsesor", "ASESOR"): True,
    ("contactarAsesor", "BACKOFFICE"): False,
    ("cobros", "CLIENTE"): False,
    ("cobros", "ASESOR"): False,
    ("cobros", "BACKOFFICE"): True,
    ("crearCobro", "CLIENTE"): False,
    ("crearCobro", "ASESOR"): False,
    ("crearCobro", "BACKOFFICE"): True,
    ("usuarios", "CLIENTE"): False,
    ("usuarios", "ASESOR"): False,
    ("usuarios", "BACKOFFICE"): True,
    ("crearUsuario", "CLIENTE"): False,
    ("crearUsuario", "ASESOR"): False,
    ("crearUsuario", "BACKOFFICE"): True,
}


@pytest.mark.parametrize(("operacion", "rol"), list(MATRIZ), ids=lambda v: str(v))
def test_matriz_de_permisos_por_rol(operacion: str, rol: str) -> None:
    query, variables = OPERACIONES[operacion]
    with _cliente() as client:
        cuerpo = _post(client, query, variables, token_de_prueba(rol))

    if MATRIZ[(operacion, rol)]:
        assert _error(cuerpo) is None
    else:
        assert _error(cuerpo) == MENSAJE_SIN_PERMISO


def test_modo_opcional_sin_token_conserva_el_comportamiento_anterior() -> None:
    query, variables = OPERACIONES["solicitarOferta"]
    with _cliente("opcional") as client:
        cuerpo = _post(client, query, variables)

    assert _error(cuerpo) is None
    assert cuerpo["data"]["solicitarOferta"]["__typename"] == "Oferta"


def test_modo_obligatoria_sin_token_rechaza() -> None:
    query, variables = OPERACIONES["solicitarOferta"]
    with _cliente("obligatoria") as client:
        assert _error(_post(client, query, variables)) == MENSAJE_SIN_SESION


@pytest.mark.parametrize("modo", ["opcional", "obligatoria"])
def test_gestion_de_usuarios_exige_token_en_cualquier_modo(modo: str) -> None:
    with _cliente(modo) as client:
        assert _error(_post(client, "{ usuarios { id } }")) == MENSAJE_SIN_SESION
        assert _error(_post(client, "{ yo { id } }")) == MENSAJE_SIN_SESION


@pytest.mark.parametrize("modo", ["opcional", "obligatoria"])
def test_token_vencido_o_alterado_siempre_se_rechaza(modo: str) -> None:
    query, variables = OPERACIONES["solicitarOferta"]
    vencido = token_de_prueba("ASESOR", segundos=-10)
    with _cliente(modo) as client:
        assert _error(_post(client, query, variables, vencido)) == MENSAJE_TOKEN_INVALIDO
        assert _error(_post(client, query, variables, "no.es.jwt")) == MENSAJE_TOKEN_INVALIDO


def test_iniciar_sesion_y_yo() -> None:
    login = """mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "Clave-segura-1"}) {
        __typename ... on Sesion { token expiraEn usuario { usuario rol } } } }"""
    with _cliente() as client:
        sesion = _post(client, login)["data"]["iniciarSesion"]
        yo = _post(client, "{ yo { id usuario rol estado } }", token=sesion["token"])["data"]["yo"]

    assert sesion["__typename"] == "Sesion"
    assert sesion["usuario"] == {"usuario": "asesor.uno", "rol": "ASESOR"}
    assert yo == {"id": "u-1", "usuario": "asesor.uno", "rol": "ASESOR", "estado": "ACTIVO"}


def test_clave_erronea_devuelve_error_de_autenticacion_sin_5xx() -> None:
    login = """mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "Mala-clave-9"}) {
        __typename ... on ErrorAutenticacion { mensaje } } }"""
    with _cliente() as client:
        resultado = _post(client, login)["data"]["iniciarSesion"]

    assert resultado == {
        "__typename": "ErrorAutenticacion",
        "mensaje": "Usuario o clave incorrectos.",
    }


def test_backoffice_crea_cambia_rol_y_suspende() -> None:
    bo = token_de_prueba("BACKOFFICE", "u-bo")
    crear = """mutation { crearUsuario(entrada: {usuario: "cliente.dos", clave: "Clave-segura-1",
        rol: CLIENTE}) { __typename ... on Usuario { id rol estado } } }"""
    with _cliente() as client:
        creado = _post(client, crear, token=bo)["data"]["crearUsuario"]
        rol = _post(
            client,
            "mutation($id: ID!) { cambiarRolUsuario(id: $id, rol: ASESOR) "
            "{ ... on Usuario { rol } } }",
            {"id": creado["id"]},
            bo,
        )["data"]["cambiarRolUsuario"]
        suspendido = _post(
            client,
            "mutation($id: ID!) { suspenderUsuario(id: $id) { ... on Usuario { estado } } }",
            {"id": creado["id"]},
            bo,
        )["data"]["suspenderUsuario"]

    assert creado["__typename"] == "Usuario" and creado["rol"] == "CLIENTE"
    assert rol == {"rol": "ASESOR"}
    assert suspendido == {"estado": "SUSPENDIDO"}


def test_alias_invalido_devuelve_error_usuario() -> None:
    crear = """mutation { crearUsuario(entrada: {usuario: "a@b.co", clave: "Clave-segura-1",
        rol: CLIENTE}) { __typename ... on ErrorUsuario { mensaje } } }"""
    with _cliente() as client:
        resultado = _post(client, crear, token=token_de_prueba("BACKOFFICE"))["data"][
            "crearUsuario"
        ]

    assert resultado["__typename"] == "ErrorUsuario"
    assert "correo" in resultado["mensaje"]


def test_suspender_usuario_inexistente() -> None:
    query = 'mutation { suspenderUsuario(id: "no-existe") { ... on ErrorUsuario { mensaje } } }'
    with _cliente() as client:
        resultado = _post(client, query, token=token_de_prueba("BACKOFFICE"))

    assert resultado["data"]["suspenderUsuario"] == {"mensaje": "Usuario no encontrado."}


def test_el_actor_del_consentimiento_es_el_asesor_autenticado() -> None:
    auth = AuthFalso()
    query, _ = OPERACIONES["registrarConsentimiento"]
    with _cliente(auth=auth) as client:
        _post(client, query, token=token_de_prueba("ASESOR", "u-asesor"))
        _post(client, query)  # sin sesión, modo opcional

    assert auth.actores == [{"rol": "ASESOR", "usuario_id": "u-asesor"}, None]


def test_los_logs_no_llevan_clave_ni_token(caplog) -> None:
    token = token_de_prueba("ASESOR")
    login = """mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "Clave-segura-1"}) {
        __typename } }"""
    query, variables = OPERACIONES["solicitarOferta"]
    with caplog.at_level(logging.DEBUG), _cliente() as client:
        _post(client, login)
        _post(client, query, variables, token)

    assert "Clave-segura-1" not in caplog.text
    assert token not in caplog.text


def test_cada_usuario_ve_su_propio_nombre_en_el_login_y_en_yo() -> None:
    auth = AuthFalso()
    auth.nombres = {"u-1": "Laura Gómez"}
    login = """mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "Clave-segura-1"}) {
        ... on Sesion { token usuario { nombre rol } } } }"""
    with _cliente(auth=auth) as client:
        sesion = _post(client, login)["data"]["iniciarSesion"]
        yo = _post(client, "{ yo { nombre } }", token=sesion["token"])["data"]["yo"]

    assert sesion["usuario"] == {"nombre": "Laura Gómez", "rol": "ASESOR"}
    assert yo == {"nombre": "Laura Gómez"}


def test_un_usuario_sin_nombre_recibe_null_y_el_front_muestra_el_alias() -> None:
    with _cliente() as client:
        yo = _post(client, "{ yo { usuario nombre } }", token=token_de_prueba("ASESOR", "u-1"))

    assert yo["data"]["yo"] == {"usuario": "asesor.uno", "nombre": None}


def test_el_nombre_no_viaja_en_el_jwt() -> None:
    import jwt

    auth = AuthFalso()
    auth.nombres = {"u-1": "Laura Gómez"}
    with _cliente(auth=auth) as client:
        token = _post(
            client,
            """mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "Clave-segura-1"}) {
                ... on Sesion { token } } }""",
        )["data"]["iniciarSesion"]["token"]

    assert "nombre" not in jwt.decode(token, options={"verify_signature": False})


def test_el_listado_de_usuarios_no_expone_nombres() -> None:
    auth = AuthFalso()
    auth.nombres = {"u-1": "Laura Gómez", "u-bo": "Admin Solventa"}
    with _cliente(auth=auth) as client:
        usuarios = _post(
            client, "{ usuarios { usuario nombre } }", token=token_de_prueba("BACKOFFICE", "u-bo")
        )["data"]["usuarios"]

    assert all(u["nombre"] is None for u in usuarios)


def test_iniciar_sesion_con_otro_rol_responde_como_clave_erronea() -> None:
    """T17: el chip de rol equivocado no revela el rol real de la cuenta ni abre sesión."""
    plantilla = (
        'mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "Clave-segura-1", '
        "rol: %s}) { __typename ... on ErrorAutenticacion { mensaje codigo } "
        "... on Sesion { token } } }"
    )
    with _cliente() as client:
        otro = _post(client, plantilla % "BACKOFFICE")["data"]["iniciarSesion"]
        propio = _post(client, plantilla % "ASESOR")["data"]["iniciarSesion"]

    assert otro == {
        "__typename": "ErrorAutenticacion",
        "mensaje": "Usuario o clave incorrectos.",
        "codigo": None,
    }
    assert propio["__typename"] == "Sesion"
