"""Contrato v0.38 (T20, KAN-26): tipo de documento y país en el consentimiento, la cotización, el
alta de usuarios y las preferencias. El número se valida con el formato del país y el tipo y el
país entran al titular, salvo la cédula colombiana, que conserva el titular de siempre."""

import pytest
from fakes import AuthFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import CLAVE_CLIENTE_ID_LOCAL, Configuracion
from app.identificadores import cliente_id_desde_documento
from app.jurisdicciones import documento_tipado
from app.main import create_app

CONSENTIR = """mutation($e: ConsentimientoInput!) {
  registrarConsentimiento(entrada: $e) { id }
}"""
COTIZAR = """mutation($e: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $e) { ... on ErrorOferta { mensaje motivo } ... on Oferta { id } }
}"""


def _post(client: TestClient, query: str, variables: dict, rol: str = "ASESOR", uid: str = "u-1"):
    r = client.post(
        "/graphql/web",
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, uid)}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _app(auth: AuthFalso) -> TestClient:
    return TestClient(
        create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    )


@pytest.mark.parametrize(
    ("numero", "tipo", "pais", "esperado"),
    [
        ("1.020.304.050", None, None, None),  # sin tipo ni país: tal cual (validación de antes)
        ("1.020.304.050", "CC", "CO", ("1020304050", "CC", "CO")),
        ("12345678", None, "pe", ("12345678", "DNI", "PE")),
        ("123.456.789-09", "cpf", "BR", ("12345678909", "CPF", "BR")),
        ("1020304050", "CURP", "CO", "invalido"),
        ("1020304050", "CC", "AR", "invalido"),
    ],
)
def test_documento_tipado(numero, tipo, pais, esperado) -> None:
    resultado = documento_tipado(numero, tipo, pais)
    if esperado == "invalido":
        assert resultado is None
    elif esperado is None:
        assert resultado == (numero, None, None)
    else:
        assert resultado == esperado


def test_el_consentimiento_con_tipo_y_pais_guarda_el_titular_compuesto() -> None:
    auth = AuthFalso()
    entrada = {
        "finalidad": "cotizacion",
        "versionTexto": "v1",
        "documentoIdentidad": "998877",
        "tipoDocumento": "CE",
        "pais": "CO",
    }
    with _app(auth) as client:
        _post(client, CONSENTIR, {"e": entrada})
        _post(client, CONSENTIR, {"e": {**entrada, "tipoDocumento": None, "pais": None}})

    compuesto, de_siempre = (r["titular_id"] for r in auth.registros_consentimiento)
    assert compuesto == cliente_id_desde_documento("998877", CLAVE_CLIENTE_ID_LOCAL, "CO", "CE")
    assert de_siempre == cliente_id_desde_documento("998877", CLAVE_CLIENTE_ID_LOCAL)
    assert compuesto != de_siempre


def test_el_consentimiento_rechaza_un_documento_que_no_es_del_pais() -> None:
    entrada = {
        "finalidad": "cotizacion",
        "versionTexto": "v1",
        "documentoIdentidad": "1020304050",
        "tipoDocumento": "CURP",
        "pais": "MX",
    }
    with _app(AuthFalso()) as client:
        cuerpo = _post(client, CONSENTIR, {"e": entrada})

    assert "no es válido para ese tipo y país" in cuerpo["errors"][0]["message"]


def test_la_cotizacion_rechaza_un_documento_que_no_es_del_pais() -> None:
    entrada = {
        "consentimientoId": "c-1",
        "documentoIdentidad": "1234",
        "tipoDocumento": "DNI",
        "pais": "PE",
        "valorCredito": "100000000",
        "plazoMeses": 60,
        "direccionInmueble": "Calle 1",
    }
    with _app(AuthFalso()) as client:
        cuerpo = _post(client, COTIZAR, {"e": entrada})

    assert cuerpo["data"]["solicitarOferta"] == {
        "mensaje": "El documento de identidad no es válido para ese tipo y país.",
        "motivo": "DATOS_INSUFICIENTES",
    }


def test_alta_y_preferencias_con_pais() -> None:
    auth = AuthFalso()
    alta = {
        "usuario": "asesor.mx",
        "clave": "Clave-segura-1",
        "rol": "ASESOR",
        "pais": "MX",
    }
    with _app(auth) as client:
        creado = _post(
            client,
            "mutation($e: CrearUsuarioInput!) "
            "{ crearUsuario(entrada: $e) { ... on Usuario { id } } }",
            {"e": alta},
            rol="BACKOFFICE",
        )["data"]["crearUsuario"]
        cambiado = _post(
            client,
            "mutation($e: PreferenciasInput!) "
            "{ actualizarPreferencias(entrada: $e) "
            "{ ... on Usuario { idioma pais tipoDocumento } } }",
            {"e": {"idioma": "EN", "moneda": "MXN", "pais": "MX"}},
            uid=creado["id"],
        )["data"]["actualizarPreferencias"]

    assert cambiado == {"idioma": "EN", "pais": "MX", "tipoDocumento": None}


# ---------- v0.39 (T19): placeId en la cotización


def _cotizar_con_lugar(place_id: str | None, fallan: set[str] | None = None):
    from fakes import AclFalso

    acl = AclFalso()
    if fallan:
        acl.fallan = fallan
    entrada = {
        "consentimientoId": "c-1",
        "documentoIdentidad": "1020304050",
        "valorCredito": "100000000",
        "plazoMeses": 60,
        "direccionInmueble": "calle 10 5 20",
        "placeId": place_id,
    }
    clientes = clientes_falsos(auth=AuthFalso(), acl=acl)
    with TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes)) as c:
        cuerpo = _post(c, COTIZAR, {"e": entrada})
    return cuerpo["data"]["solicitarOferta"], clientes


def test_lugar_de_un_pais_no_soportado_se_rechaza() -> None:
    resultado, _ = _cotizar_con_lugar("stub:AR:Av. Corrientes 1234")

    assert resultado == {
        "mensaje": "La dirección del inmueble está en un país que no atendemos.",
        "motivo": None,
    }


@pytest.mark.parametrize(
    ("place_id", "fallan", "esperada"),
    [
        ("stub:CO:Calle 10 # 5-20, Bogotá", None, "Calle 10 # 5-20, Bogotá"),  # normalizada
        ("stub:CO:Calle 10 # 5-20, Bogotá", {"lugares"}, "calle 10 5 20"),  # sin verificar
        (None, None, "calle 10 5 20"),  # sin placeId: la escrita
    ],
)
def test_cotiza_con_la_direccion_normalizada_o_la_escrita(place_id, fallan, esperada) -> None:
    resultado, clientes = _cotizar_con_lugar(place_id, fallan)

    assert "id" in resultado, resultado
    assert clientes.rating.llamadas[-1][4] == esperada
