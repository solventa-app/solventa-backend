"""Esqueleto de la v0.18 (HU-W03, bloque 9): las operaciones existen, nunca dan 5xx y, hasta F-B6,
responden «aún no disponible» con reintento. Lo que ya funciona: el estado visible de la propuesta,
el contacto opcional en `aceptarOferta` y la cédula aún obligatoria en `solicitarOferta`."""

import pytest
from fakes import PolicyFalso, RatingFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.esquema import MENSAJE_OPERACION_NO_DISPONIBLE, EstadoVisible, estado_visible
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


@pytest.mark.parametrize(
    ("consulta", "campo", "rol"),
    [
        (
            'mutation { enviarCodigoVerificacion(propuestaId: "p-1") '
            "{ ... on ErrorCodigo { mensaje puedeReintentar } } }",
            "enviarCodigoVerificacion",
            "ASESOR",
        ),
        (
            'mutation { verificarCodigo(propuestaId: "p-1", codigo: "123456") '
            "{ ... on ErrorCodigo { mensaje puedeReintentar } } }",
            "verificarCodigo",
            "ASESOR",
        ),
        (
            'mutation { emitirPropuesta(propuestaId: "p-1") '
            "{ ... on ErrorEmision { mensaje puedeReintentar } } }",
            "emitirPropuesta",
            "CLIENTE",
        ),
        (
            'mutation { reenviarEnlaceFirma(propuestaId: "p-1") '
            "{ ... on ErrorEmision { mensaje puedeReintentar } } }",
            "reenviarEnlaceFirma",
            "ASESOR",
        ),
        (
            'mutation { firmarPorEnlace(entrada: {versionDocumento: "v1", aceptaTerminos: true, '
            'autorizaCobro: true, modo: NOMBRE, nombreFirma: "Ana"}) '
            "{ ... on ErrorFirma { mensaje puedeReintentar } } }",
            "firmarPorEnlace",
            "CLIENTE",
        ),
        (
            "mutation { iniciarPago { ... on ErrorPago { mensaje puedeReintentar } } }",
            "iniciarPago",
            "CLIENTE",
        ),
        (
            'mutation { confirmarPago(sesionId: "s-1") '
            "{ ... on ErrorPago { mensaje puedeReintentar } } }",
            "confirmarPago",
            "CLIENTE",
        ),
    ],
)
def test_las_operaciones_nuevas_responden_no_disponible_con_reintento(
    consulta: str, campo: str, rol: str
) -> None:
    with _cliente() as client:
        cuerpo = _post(client, consulta, rol=rol)
    assert cuerpo["data"][campo] == {
        "mensaje": MENSAJE_OPERACION_NO_DISPONIBLE,
        "puedeReintentar": True,
    }


def test_el_codigo_de_verificacion_es_solo_del_asesor() -> None:
    consulta = 'mutation { enviarCodigoVerificacion(propuestaId: "p-1") { __typename } }'
    with _cliente() as client:
        cuerpo = _post(client, consulta, rol="CLIENTE")
    assert cuerpo["data"] is None
    assert cuerpo["errors"][0]["message"] == "No tienes permiso para esta operación."


def test_propuestas_y_ticket_responden_error_publico_sin_5xx() -> None:
    with _cliente() as client:
        lista = _post(client, "{ propuestas { total } }", rol="BACKOFFICE")
        ticket = _post(client, "mutation { ticketAvisos { ticket } }", rol="CLIENTE")
        enlace = _post(client, "{ firmaPorEnlace { vigente } }", rol="CLIENTE")
    assert lista["errors"][0]["message"] == MENSAJE_OPERACION_NO_DISPONIBLE
    assert ticket["errors"][0]["message"] == MENSAJE_OPERACION_NO_DISPONIBLE
    assert enlace["data"] == {"firmaPorEnlace": None}
