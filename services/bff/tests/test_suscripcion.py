"""Aceptar una oferta y consultar la propuesta (HU-W03, KAN-209, CA-W03-01), con dobles."""

from contextlib import contextmanager

import pytest
from fakes import (
    CONSENTIMIENTO_VIGENTE,
    OFERTA_COMPLETA,
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
PROPUESTA_CON_MOTIVO = """query($id: ID!) {
  propuesta(id: $id) { estado motivoEstado mensajeEstado accionSugerida }
}"""
OFERTA = """query($id: ID!) {
  oferta(id: $id) {
    id tipo
    prima { valor moneda }
    cobertura { valor moneda }
    desgloseScore { nombre peso }
    fuentes { id nombre origen estado }
    perfilRiesgo { nivelRiesgo }
  }
}"""

# Lo que RATING guardó al calcular la oferta (`GET /ofertas/{id}`).
GUARDADA = {
    "id": "o-1",
    "tipo": "COMPLETA",
    "clienteId": "cliente-opaco",
    "consentimientoId": "c-1",
}

# La oferta completa tal como la guarda RATING: lo que devolvió `POST /ofertas` más el titular y el
# consentimiento (`almacen_ofertas.py`).
GUARDADA_COMPLETA = {
    **OFERTA_COMPLETA,
    "clienteId": "cliente-opaco",
    "consentimientoId": "c-1",
    "plazoMeses": 60,
    "vigenteHasta": "2026-11-01T12:00:00+00:00",
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


ACEPTAR_CON_CONTACTO = """mutation($id: ID!, $c: ContactoTitularInput) {
  aceptarOferta(ofertaId: $id, contacto: $c) {
    __typename
    ... on Propuesta { id }
    ... on ErrorAceptacion { mensaje puedeReintentar }
  }
}"""
CONTACTO = {"nombre": "Ana Pérez", "correo": "ana@correo.com"}


def test_un_asesor_puede_aceptar_por_el_cliente() -> None:
    consentimiento = {**CONSENTIMIENTO_VIGENTE, "actor": {"rol": "CLIENTE", "usuario_id": "u-otro"}}
    with _cliente(auth=AuthFalso({"c-1": consentimiento})) as client:
        propuesta = _post(client, ACEPTAR_CON_CONTACTO, {"id": "o-1", "c": CONTACTO}, rol="ASESOR")[
            "data"
        ]["aceptarOferta"]

    assert propuesta["__typename"] == "Propuesta"


def test_un_asesor_sin_el_contacto_del_titular_no_acepta() -> None:
    """Sin contacto no hay a quién mandar el código: la propuesta quedaría trabada (F-B9)."""
    consentimiento = {**CONSENTIMIENTO_VIGENTE, "actor": {"rol": "CLIENTE", "usuario_id": "u-otro"}}
    policy = PolicyFalso()
    with _cliente(auth=AuthFalso({"c-1": consentimiento}), policy=policy) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"}, rol="ASESOR")["data"]["aceptarOferta"]

    assert error["__typename"] == "ErrorAceptacion"
    assert error["mensaje"] == "Revisa el nombre y el correo del titular."
    assert error["puedeReintentar"] is False


def test_oferta_preliminar_la_rechaza_policy_con_su_motivo() -> None:
    with _cliente(policy=PolicyFalso(rechazo="OFERTA_PRELIMINAR")) as client:
        error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

    assert error["motivo"] == "OFERTA_PRELIMINAR"
    # D-11: la preliminar no se acepta, se ofrece recalcularla (volver a cotizar).
    assert error["puedeReintentar"] is True
    assert "preliminar" in error["mensaje"]
    assert "Vuelve a calcularla" in error["mensaje"]


def test_los_demas_rechazos_de_la_aceptacion_no_ofrecen_reintentar() -> None:
    for motivo in ("OFERTA_NO_ENCONTRADA", "OFERTA_VENCIDA"):
        with _cliente(policy=PolicyFalso(rechazo=motivo)) as client:
            error = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]

        assert error["motivo"] == motivo
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


# ---------- oferta(id): la oferta que RATING guardó (CA-W03-01) ----------


def _cliente_con_oferta(auth: AuthFalso | None = None, **rating) -> TestClient:
    guardadas = {"o-1": GUARDADA_COMPLETA}
    return _cliente(auth=auth, rating=RatingFalso(guardadas=guardadas, **rating))


def test_consulta_la_oferta_guardada_con_sus_fuentes() -> None:
    with _cliente_con_oferta() as client:
        oferta = _post(client, OFERTA, {"id": "o-1"})["data"]["oferta"]

    assert oferta["id"] == "o-1"
    assert oferta["tipo"] == "COMPLETA"
    assert oferta["prima"] == {"valor": "61200.00", "moneda": "COP"}
    assert oferta["fuentes"] == [
        {
            "id": "historial-crediticio",
            "nombre": "historial-crediticio",
            "origen": "OPEN_FINANCE",
            "estado": "OK",
        }
    ]
    assert oferta["desgloseScore"] == [{"nombre": "historial-crediticio", "peso": 0.3}]
    assert oferta["perfilRiesgo"] == {"nivelRiesgo": "BAJO"}


def test_una_oferta_vencida_o_desconocida_es_null() -> None:
    with _cliente_con_oferta() as client:
        assert _post(client, OFERTA, {"id": "o-vencida"})["data"]["oferta"] is None


def test_un_cliente_no_ve_la_oferta_que_consintio_otro_usuario() -> None:
    consentimiento = {**CONSENTIMIENTO_VIGENTE, "actor": {"rol": "CLIENTE", "usuario_id": "u-otro"}}
    with _cliente_con_oferta(auth=AuthFalso({"c-1": consentimiento})) as client:
        assert _post(client, OFERTA, {"id": "o-1"}, uid="u-1")["data"]["oferta"] is None


def test_un_asesor_ve_la_oferta_del_cliente() -> None:
    consentimiento = {**CONSENTIMIENTO_VIGENTE, "actor": {"rol": "CLIENTE", "usuario_id": "u-otro"}}
    with _cliente_con_oferta(auth=AuthFalso({"c-1": consentimiento})) as client:
        oferta = _post(client, OFERTA, {"id": "o-1"}, rol="ASESOR")["data"]["oferta"]

    assert oferta["id"] == "o-1"


def test_la_oferta_de_otro_titular_no_se_ve() -> None:
    auth = AuthFalso({"c-1": {**CONSENTIMIENTO_VIGENTE, "titular_id": "otro-titular"}})
    with _cliente_con_oferta(auth=auth) as client:
        assert _post(client, OFERTA, {"id": "o-1"})["data"]["oferta"] is None


def test_sin_consentimiento_vigente_la_oferta_no_se_ve() -> None:
    auth = AuthFalso({"c-1": {**CONSENTIMIENTO_VIGENTE, "vigente": False, "estado": "REVOCADO"}})
    with _cliente_con_oferta(auth=auth) as client:
        assert _post(client, OFERTA, {"id": "o-1"})["data"]["oferta"] is None


def test_si_rating_no_responde_la_oferta_es_un_error_no_un_null() -> None:
    with _cliente_con_oferta(error_lectura=ErrorServicio("caído")) as client:
        cuerpo = _post(client, OFERTA, {"id": "o-1"})

    assert cuerpo["data"]["oferta"] is None
    assert cuerpo["errors"][0]["message"] == "No pudimos procesar tu suscripción. Intenta de nuevo."


def test_si_auth_no_responde_la_oferta_es_un_error_no_un_null() -> None:
    with _cliente_con_oferta(auth=AuthFalso(falla=True)) as client:
        cuerpo = _post(client, OFERTA, {"id": "o-1"})

    assert cuerpo["data"]["oferta"] is None
    assert cuerpo["errors"][0]["message"] == "No pudimos procesar tu suscripción. Intenta de nuevo."


def test_el_back_office_no_consulta_ofertas() -> None:
    with _cliente_con_oferta() as client:
        cuerpo = _post(client, OFERTA, {"id": "o-1"}, rol="BACKOFFICE")

    assert cuerpo["data"]["oferta"] is None
    assert cuerpo["errors"][0]["message"] == "No tienes permiso para esta operación."


# ---------- Qué le pasa a la propuesta y qué hacer (CA-W03-06) ----------

CODIGOS_INTERNOS = (
    "KYC_RECHAZADO",
    "KYC_NO_DISPONIBLE",
    "RIESGO_ALTO",
    "RIESGO_DESCONOCIDO",
    "COBERTURA_ALTA",
    "MONEDA_NO_SOPORTADA",
    "COBRO_EN_CONFLICTO",
    "acl_no_disponible",
    "fondos_insuficientes",
    "tarjeta_vencida",
)


@contextmanager
def _propuesta_en(estado: str, motivo_estado: str | None):
    """Un cliente con una propuesta ya aceptada y puesta en `estado`; entrega `(client, id)`."""
    policy = PolicyFalso()
    with _cliente(policy=policy) as client:
        creada = _post(client, ACEPTAR, {"id": "o-1"})["data"]["aceptarOferta"]
        policy.propuestas["o-1"].update(estado=estado, motivo_estado=motivo_estado)
        yield client, creada["id"]


@pytest.mark.parametrize(
    ("estado", "motivo_estado", "motivo", "accion"),
    [
        ("PAGO_RECHAZADO", "fondos_insuficientes", "PAGO_FONDOS_INSUFICIENTES", "CONTACTAR_ASESOR"),
        ("PAGO_RECHAZADO", "tarjeta_vencida", "PAGO_RECHAZADO", "CONTACTAR_ASESOR"),
        ("PAGO_RECHAZADO", None, "PAGO_RECHAZADO", "CONTACTAR_ASESOR"),
        ("PENDIENTE_PAGO", "acl_no_disponible", "PAGO_EN_PROCESO", "ESPERAR"),
        ("PENDIENTE_PAGO", None, "PAGO_EN_PROCESO", "ESPERAR"),
        ("EN_REVISION", "KYC_NO_DISPONIBLE,RIESGO_ALTO", "REVISION_MANUAL", "ESPERAR"),
        ("EN_REVISION", "COBRO_EN_CONFLICTO", "REVISION_MANUAL", "ESPERAR"),
        ("NO_EMITIDA", "KYC_RECHAZADO", "NO_APROBADA", "CONTACTAR_ASESOR"),
    ],
)
def test_el_estado_que_no_avanza_dice_por_que_y_que_hacer(
    estado: str, motivo_estado: str | None, motivo: str, accion: str
) -> None:
    with _propuesta_en(estado, motivo_estado) as (client, propuesta_id):
        cuerpo = _post(client, PROPUESTA_CON_MOTIVO, {"id": propuesta_id})

    propuesta = cuerpo["data"]["propuesta"]
    assert propuesta["estado"] == estado
    assert propuesta["motivoEstado"] == motivo
    assert propuesta["accionSugerida"] == accion
    assert propuesta["mensajeEstado"]
    # Ni el código de UNDER ni el de la pasarela llegan al cliente, en ningún campo.
    texto = str(cuerpo)
    for codigo in CODIGOS_INTERNOS:
        assert codigo not in texto


@pytest.mark.parametrize("estado", ["ACEPTADA", "EN_EMISION", "EMITIDA"])
def test_los_estados_sin_novedad_no_traen_motivo(estado: str) -> None:
    with _propuesta_en(estado, None) as (client, propuesta_id):
        propuesta = _post(client, PROPUESTA_CON_MOTIVO, {"id": propuesta_id})["data"]["propuesta"]

    assert propuesta["estado"] == estado
    assert propuesta["motivoEstado"] is None
    assert propuesta["mensajeEstado"] is None
    assert propuesta["accionSugerida"] is None


def test_el_mensaje_del_estado_sale_en_ingles_si_el_usuario_lo_pide() -> None:
    with _propuesta_en("EN_REVISION", "RIESGO_ALTO") as (client, propuesta_id):
        r = client.post(
            "/graphql/web",
            json={"query": PROPUESTA_CON_MOTIVO, "variables": {"id": propuesta_id}},
            headers={
                "Authorization": f"Bearer {token_de_prueba('CLIENTE', usuario_id='u-1')}",
                "Accept-Language": "en",
            },
        )

    propuesta = r.json()["data"]["propuesta"]
    assert propuesta["mensajeEstado"] == (
        "We're reviewing your request. We'll let you know when it's ready."
    )
    assert propuesta["motivoEstado"] == "REVISION_MANUAL"  # el código no se traduce


def test_aceptar_una_oferta_no_trae_motivo_de_estado() -> None:
    consulta = """mutation($id: ID!) {
      aceptarOferta(ofertaId: $id) {
        ... on Propuesta { estado motivoEstado accionSugerida }
      }
    }"""
    with _cliente() as client:
        propuesta = _post(client, consulta, {"id": "o-1"})["data"]["aceptarOferta"]

    assert propuesta == {"estado": "ACEPTADA", "motivoEstado": None, "accionSugerida": None}


def test_una_propuesta_cancelada_no_lleva_mensaje_de_estado() -> None:
    """El contrato lista solo cuatro estados con `mensajeEstado`; `CANCELADA` se ve como
    «no emitida» pero no inventa una causa."""
    from app.servicio_suscripcion import explicar_estado

    assert explicar_estado({"estado": "CANCELADA"}) is None
