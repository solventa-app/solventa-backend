"""Contrato v0.18 (HU-W03, bloque 9): estado visible de la propuesta, contacto opcional en
`aceptarOferta`, permisos por rol de las operaciones nuevas y documento en `crearUsuario`/`yo`.
El comportamiento de la firma y el pago está en `test_firma_pago.py`."""

import pytest
from fakes import PolicyFalso, RatingFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.esquema import EstadoVisible, estado_visible
from app.main import create_app

GUARDADA = {
    "id": "o-1",
    "tipo": "COMPLETA",
    "clienteId": "cliente-opaco",
    "consentimientoId": "c-1",
}


def _cliente() -> TestClient:
    return TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(
                rating=RatingFalso(guardadas={"o-1": GUARDADA}), policy=PolicyFalso()
            ),
        )
    )


def _post(client: TestClient, query: str, variables: dict | None = None, rol: str = "ASESOR"):
    r = client.post(
        "/graphql/web",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


@pytest.mark.parametrize(
    ("estado", "visible"),
    [
        ("ACEPTADA", EstadoVisible.EMITIENDO),
        ("IDENTIDAD_VERIFICADA", EstadoVisible.EMITIENDO),
        ("EN_FIRMA", EstadoVisible.EMITIENDO),
        ("FIRMADA", EstadoVisible.EMITIENDO),
        ("EN_EMISION", EstadoVisible.EMITIENDO),
        ("PENDIENTE_PAGO", EstadoVisible.EMITIENDO),
        ("PAGO_RECHAZADO", EstadoVisible.EMITIENDO),
        ("EN_REVISION", EstadoVisible.EMITIENDO),
        ("EMITIDA", EstadoVisible.EMITIDA),
        ("NO_EMITIDA", EstadoVisible.NO_EMITIDA),
        ("CANCELADA", EstadoVisible.NO_EMITIDA),
    ],
)
def test_estado_visible_de_la_tabla_w03_35(estado: str, visible: EstadoVisible) -> None:
    assert estado_visible(estado) is visible


def test_aceptar_con_contacto_devuelve_la_propuesta_con_su_estado_visible() -> None:
    consulta = """mutation($id: ID!, $c: ContactoTitularInput) {
      aceptarOferta(ofertaId: $id, contacto: $c) { ... on Propuesta { estado estadoVisible } }
    }"""
    with _cliente() as client:
        contacto = {"nombre": "Ana Pérez", "correo": "ana@correo.com"}
        cuerpo = _post(client, consulta, {"id": "o-1", "c": contacto})
    assert cuerpo["data"]["aceptarOferta"] == {"estado": "ACEPTADA", "estadoVisible": "EMITIENDO"}


def test_solicitar_oferta_sin_documento_responde_datos_insuficientes() -> None:
    consulta = """mutation {
      solicitarOferta(entrada: {consentimientoId: "c-1", valorCredito: "100000000",
                                plazoMeses: 60, direccionInmueble: "Calle 1"}) {
        ... on ErrorOferta { motivo puedeReintentar }
      }
    }"""
    with _cliente() as client:
        cuerpo = _post(client, consulta)
    assert cuerpo["data"]["solicitarOferta"] == {
        "motivo": "DATOS_INSUFICIENTES",
        "puedeReintentar": False,
    }


def test_el_codigo_de_verificacion_es_solo_del_asesor() -> None:
    consulta = 'mutation { enviarCodigoVerificacion(propuestaId: "p-1") { __typename } }'
    with _cliente() as client:
        cuerpo = _post(client, consulta, rol="CLIENTE")
    assert cuerpo["data"] is None
    assert cuerpo["errors"][0]["message"] == "No tienes permiso para esta operación."


def test_crear_cliente_manda_el_documento_a_auth_y_yo_lo_trae_enmascarado() -> None:
    from fakes import AuthFalso

    auth = AuthFalso()
    crear = """mutation($e: CrearUsuarioInput!) {
      crearUsuario(entrada: $e) { ... on Usuario { id documentoEnmascarado } }
    }"""
    entrada = {
        "usuario": "cliente.doc",
        "clave": "Clave-segura-1",
        "rol": "CLIENTE",
        "correo": "c@ejemplo.co",
        "documentoIdentidad": "1020304050",
    }
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    with TestClient(app) as client:
        creado = _post(client, crear, {"e": entrada}, rol="BACKOFFICE")["data"]["crearUsuario"]
        r = client.post(
            "/graphql/web",
            json={"query": "{ yo { documentoEnmascarado } }"},
            headers={"Authorization": f"Bearer {token_de_prueba('CLIENTE', creado['id'])}"},
        )

    assert auth.documentos[creado["id"]] == "1020304050"
    assert creado["documentoEnmascarado"] is None  # al crear no se devuelve
    assert r.json()["data"]["yo"] == {"documentoEnmascarado": "10••••4050"}
