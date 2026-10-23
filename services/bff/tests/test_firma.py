"""Documento de firma y firma de la propuesta (HU-W03, KAN-211, CA-W03-02 y 03), con dobles."""

from fakes import (
    AuthFalso,
    PolicyFalso,
    RatingFalso,
    clientes_falsos,
    token_de_prueba,
)
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.main import create_app

ACEPTAR = """mutation($id: ID!) { aceptarOferta(ofertaId: $id) { ... on Propuesta { id } } }"""
DOCUMENTO = """query($id: ID!) {
  documentoFirma(propuestaId: $id) {
    propuestaId version resumen meses formaCobro montoMensual { valor moneda }
  }
}"""
FIRMAR = """mutation($e: FirmarPropuestaInput!) {
  firmarPropuesta(entrada: $e) {
    __typename
    ... on FirmaRegistrada { propuestaId firmadaEn referencia estado }
    ... on ErrorFirma { mensaje puedeReintentar motivo }
  }
}"""
GUARDADA = {
    "id": "o-1",
    "tipo": "COMPLETA",
    "clienteId": "cliente-opaco",
    "consentimientoId": "c-1",
}


def _cliente(policy: PolicyFalso | None = None) -> TestClient:
    return TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(
                auth=AuthFalso(),
                rating=RatingFalso(guardadas={"o-1": GUARDADA}),
                policy=policy,
            ),
        )
    )


def _post(client: TestClient, query: str, variables: dict, rol: str = "CLIENTE", uid: str = "u-1"):
    r = client.post(
        "/graphql/web",
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _entrada(propuesta_id: str, **cambios) -> dict:
    return {
        "e": {
            "propuestaId": propuesta_id,
            "autorizaCobro": True,
            "versionDocumento": "v1",
            **cambios,
        }
    }


def _propuesta(client: TestClient, uid: str = "u-1") -> str:
    return _post(client, ACEPTAR, {"id": "o-1"}, uid=uid)["data"]["aceptarOferta"]["id"]


def test_el_cliente_lee_el_documento_con_el_monto_de_la_clausula() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client)
        documento = _post(client, DOCUMENTO, {"id": propuesta_id})["data"]["documentoFirma"]

    assert documento["montoMensual"] == {"valor": "185000", "moneda": "COP"}
    assert documento["meses"] == 60
    assert documento["version"] == "v1"
    assert documento["formaCobro"] == "con la cuota de tu crédito"


def test_un_asesor_puede_ver_el_documento() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client)
        documento = _post(client, DOCUMENTO, {"id": propuesta_id}, rol="ASESOR")["data"][
            "documentoFirma"
        ]

    assert documento["propuestaId"] == propuesta_id


def test_un_cliente_no_ve_el_documento_de_la_propuesta_de_otro() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client, uid="u-1")
        documento = _post(client, DOCUMENTO, {"id": propuesta_id}, uid="u-2")["data"]

    assert documento["documentoFirma"] is None


def test_firma_y_devuelve_la_referencia_de_la_evidencia() -> None:
    policy = PolicyFalso()
    with _cliente(policy) as client:
        propuesta_id = _propuesta(client)
        firma = _post(client, FIRMAR, _entrada(propuesta_id))["data"]["firmarPropuesta"]

    assert firma["__typename"] == "FirmaRegistrada"
    assert firma["referencia"] == f"fe-{propuesta_id}"
    assert firma["estado"] == "EN_EMISION"
    assert policy.firmas_pedidas == [
        (propuesta_id, True, "v1", "u-1")
    ]  # el firmante sale del token


def test_firmar_dos_veces_devuelve_la_misma_evidencia() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client)
        primera = _post(client, FIRMAR, _entrada(propuesta_id))["data"]["firmarPropuesta"]
        segunda = _post(client, FIRMAR, _entrada(propuesta_id))["data"]["firmarPropuesta"]

    assert primera["referencia"] == segunda["referencia"]


def test_solo_el_cliente_titular_firma_un_asesor_no() -> None:
    policy = PolicyFalso()
    with _cliente(policy) as client:
        propuesta_id = _propuesta(client)
        cuerpo = _post(client, FIRMAR, _entrada(propuesta_id), rol="ASESOR")

    assert cuerpo["data"] is None
    assert cuerpo["errors"]
    assert policy.firmas_pedidas == []


def test_firmar_exige_sesion_aunque_la_autenticacion_sea_opcional() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client)
        r = client.post("/graphql/web", json={"query": FIRMAR, "variables": _entrada(propuesta_id)})

    assert r.status_code == 200
    assert r.json()["data"] is None
    assert r.json()["errors"]


def test_un_cliente_no_firma_la_propuesta_de_otro() -> None:
    policy = PolicyFalso()
    with _cliente(policy) as client:
        propuesta_id = _propuesta(client, uid="u-1")
        error = _post(client, FIRMAR, _entrada(propuesta_id), uid="u-2")["data"]["firmarPropuesta"]

    assert error["motivo"] == "PROPUESTA_NO_FIRMABLE"
    assert error["puedeReintentar"] is False
    assert policy.firmas_pedidas == []


def test_los_rechazos_de_policy_llegan_con_su_motivo() -> None:
    casos = {
        "COBRO_NO_AUTORIZADO": True,
        "VERSION_DOCUMENTO_DESACTUALIZADA": True,
        "PROPUESTA_NO_FIRMABLE": False,
    }
    for motivo, reintentable in casos.items():
        with _cliente(PolicyFalso(rechazo_firma=motivo)) as client:
            propuesta_id = _propuesta(client)
            error = _post(client, FIRMAR, _entrada(propuesta_id))["data"]["firmarPropuesta"]

        assert error["__typename"] == "ErrorFirma"
        assert error["motivo"] == motivo
        assert error["puedeReintentar"] is reintentable


def test_el_proveedor_de_firma_caido_responde_error_con_reintento_sin_5xx() -> None:
    with _cliente(PolicyFalso(error_firma=ErrorServicio("caído"))) as client:
        propuesta_id = _propuesta(client)
        error = _post(client, FIRMAR, _entrada(propuesta_id))["data"]["firmarPropuesta"]

    assert error["motivo"] == "SERVICIO_FIRMA_NO_DISPONIBLE"
    assert error["puedeReintentar"] is True
