"""Aceptar una oferta y consultar la propuesta (HU-W03, KAN-209, CA-W03-01), con dobles."""

from fakes import (
    CONSENTIMIENTO_VIGENTE,
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

ACEPTAR = """mutation($id: ID!) {
  aceptarOferta(ofertaId: $id) {
    __typename
    ... on Propuesta {
      id ofertaId estado plazoMeses polizaId
      prima { valor moneda } cobertura { valor moneda }
    }
    ... on ErrorAceptacion { mensaje puedeReintentar motivo }
  }
}"""
PROPUESTA = """query($id: ID!) { propuesta(id: $id) { id estado ofertaId } }"""

# Lo que RATING guardó al calcular la oferta (`GET /ofertas/{id}`).
GUARDADA = {
    "id": "o-1",
    "tipo": "COMPLETA",
    "clienteId": "cliente-opaco",
    "consentimientoId": "c-1",
}


def _cliente(
    auth: AuthFalso | None = None,
    rating: RatingFalso | None = None,
    policy: PolicyFalso | None = None,
) -> TestClient:
    rating = rating or RatingFalso(guardadas={"o-1": GUARDADA})
    return TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(auth=auth, rating=rating, policy=policy),
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


def test_acepta_la_oferta_con_el_precio_que_guardo_policy() -> None:
    policy = PolicyFalso()
    with _cliente(policy=policy) as client:
        cuerpo = _post(client, ACEPTAR, {"id": "o-1"})

    propuesta = cuerpo["data"]["aceptarOferta"]
    assert propuesta["__typename"] == "Propuesta"
    assert propuesta["estado"] == "ACEPTADA"
    assert propuesta["ofertaId"] == "o-1"
    assert propuesta["prima"] == {"valor": "185000", "moneda": "COP"}
    assert propuesta["plazoMeses"] == 60
    assert propuesta["polizaId"] is None
    assert policy.llamadas == [("o-1", "u-1")]


def test_aceptar_dos_veces_devuelve_la_misma_propuesta() -> None:
    with _cliente() as client:
        primera = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]
        segunda = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert primera["id"] == segunda["id"]


def test_una_oferta_que_ya_no_existe_responde_error_sin_reintento() -> None:
    with _cliente(rating=RatingFalso(guardadas={})) as client:
        error = _post(client, ACEPTAR, {"id": "o-vencida"})["data"]["aceptarOferta"]

    assert error["__typename"] == "ErrorAceptacion"
    assert error["motivo"] == "OFERTA_NO_ENCONTRADA"
    assert error["puedeReintentar"] is False


def test_sin_consentimiento_vigente_no_llega_a_policy() -> None:
    auth = AuthFalso({"c-1": {**CONSENTIMIENTO_VIGENTE, "vigente": False, "estado": "REVOCADO"}})
    policy = PolicyFalso()
    with _cliente(auth=auth, policy=policy) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert error["motivo"] == "CONSENTIMIENTO_NO_VIGENTE"
    assert policy.llamadas == []  # regla #7: sin consentimiento no se avanza


def test_el_consentimiento_de_otro_titular_no_sirve() -> None:
    auth = AuthFalso({"c-1": {**CONSENTIMIENTO_VIGENTE, "titular_id": "otro-titular"}})
    with _cliente(auth=auth) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert error["motivo"] == "CONSENTIMIENTO_NO_VIGENTE"


def test_un_cliente_no_acepta_lo_que_consintio_otro_usuario() -> None:
    consentimiento = {**CONSENTIMIENTO_VIGENTE, "actor": {"rol": "CLIENTE", "usuario_id": "u-otro"}}
    with _cliente(auth=AuthFalso({"c-1": consentimiento})) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"}, uid="u-1")["data"]["aceptarOferta"]

    assert error["motivo"] == "CONSENTIMIENTO_NO_VIGENTE"


def test_un_asesor_puede_aceptar_por_el_cliente() -> None:
    consentimiento = {**CONSENTIMIENTO_VIGENTE, "actor": {"rol": "CLIENTE", "usuario_id": "u-otro"}}
    with _cliente(auth=AuthFalso({"c-1": consentimiento})) as client:
        propuesta = _post(client, ACEPTAR, {"id": "o-1"}, rol="ASESOR")["data"]["aceptarOferta"]

    assert propuesta["__typename"] == "Propuesta"


def test_oferta_preliminar_la_rechaza_policy_con_su_motivo() -> None:
    with _cliente(policy=PolicyFalso(rechazo="OFERTA_PRELIMINAR")) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert error["motivo"] == "OFERTA_PRELIMINAR"
    assert error["puedeReintentar"] is False


def test_policy_caido_responde_error_con_reintento_sin_5xx() -> None:
    with _cliente(policy=PolicyFalso(error=ErrorServicio("caído"))) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert error["__typename"] == "ErrorAceptacion"
    assert error["puedeReintentar"] is True


def test_rating_caido_responde_error_con_reintento_sin_5xx() -> None:
    rating = RatingFalso(error_lectura=ErrorServicio("caído"))
    with _cliente(rating=rating) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert error["puedeReintentar"] is True


def test_un_backoffice_no_puede_aceptar_ofertas() -> None:
    with _cliente() as client:
        cuerpo = _post(client, ACEPTAR, {"id": "o-1"}, rol="BACKOFFICE")

    assert cuerpo["data"] is None
    assert cuerpo["errors"]


def test_consulta_la_propuesta_del_cliente_que_la_acepto() -> None:
    with _cliente() as client:
        creada = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]
        leida = _post(client, PROPUESTA, {"id": creada["id"]})["data"]["propuesta"]

    assert leida["id"] == creada["id"]
    assert leida["estado"] == "ACEPTADA"


def test_un_cliente_no_ve_la_propuesta_de_otro() -> None:
    with _cliente() as client:
        creada = _post(client, ACEPTAR, {"id": "o-1"}, uid="u-1")["data"]["aceptarOferta"]
        ajena = _post(client, PROPUESTA, {"id": creada["id"]}, uid="u-2")["data"]["propuesta"]

    assert ajena is None


def test_una_propuesta_desconocida_es_null() -> None:
    with _cliente() as client:
        assert _post(client, PROPUESTA, {"id": "no-existe"})["data"]["propuesta"] is None


def test_si_policy_no_responde_la_consulta_es_un_error_no_un_null() -> None:
    with _cliente(policy=PolicyFalso(error=ErrorServicio("caído"))) as client:
        cuerpo = _post(client, PROPUESTA, {"id": "p-1"})

    assert cuerpo["data"]["propuesta"] is None
    assert cuerpo["errors"][0]["message"] == "No pudimos procesar tu suscripción. Intenta de nuevo."
