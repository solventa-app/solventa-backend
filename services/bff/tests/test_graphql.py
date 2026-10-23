"""GraphQL de punta a punta dentro del BFF (FastAPI + Strawberry) con dobles."""

import pytest
from fakes import OFERTA_PRELIMINAR, AuthFalso, RatingFalso, clientes_falsos
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

SOLICITAR = """
mutation($entrada: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $entrada) {
    __typename
    ... on Oferta {
      id tipo prima { valor moneda } rangoPrima { minima { valor } maxima { valor } }
      cobertura { valor moneda } desgloseScore { nombre peso }
      fuentes { id nombre origen estado capturadoEn }
      perfilRiesgo { score nivelRiesgo fechaVigencia }
    }
    ... on ErrorOferta { mensaje puedeReintentar motivo }
  }
}
"""

ENTRADA = {
    "consentimientoId": "c-1",
    "documentoIdentidad": "CC-1020304050",
    "valorCredito": "200000000",
    "plazoMeses": 60,
    "direccionInmueble": "Calle 1 # 2-3",
}


def _cliente(**fakes) -> TestClient:
    config = Configuracion.desde_entorno()
    return TestClient(create_app(config=config, clientes=clientes_falsos(**fakes)))


def _post(client: TestClient, query: str, variables: dict | None = None, canal: str = "web"):
    r = client.post(f"/graphql/{canal}", json={"query": query, "variables": variables or {}})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("canal", ["web", "app", "movil"])
def test_solicitar_oferta_completa_en_ambos_canales(canal: str) -> None:
    with _cliente() as client:
        cuerpo = _post(client, SOLICITAR, {"entrada": ENTRADA}, canal)

    oferta = cuerpo["data"]["solicitarOferta"]
    assert oferta["__typename"] == "Oferta"
    assert oferta["tipo"] == "COMPLETA"
    assert oferta["prima"] == {"valor": "61200.00", "moneda": "COP"}
    assert oferta["rangoPrima"] is None
    assert oferta["perfilRiesgo"]["score"] == 82
    assert oferta["perfilRiesgo"]["nivelRiesgo"] == "BAJO"


def test_oferta_preliminar_trae_rango_y_fecha_de_captura() -> None:
    with _cliente(rating=RatingFalso(oferta=OFERTA_PRELIMINAR)) as client:
        oferta = _post(client, SOLICITAR, {"entrada": ENTRADA})["data"]["solicitarOferta"]

    assert oferta["tipo"] == "PRELIMINAR"
    assert oferta["prima"] is None
    assert oferta["rangoPrima"]["minima"]["valor"] == "48960.00"
    assert oferta["fuentes"][0]["estado"] == "CACHE"
    assert oferta["fuentes"][0]["capturadoEn"].startswith("2026-10-08T11:55:00")


def test_sin_consentimiento_responde_error_oferta_no_5xx() -> None:
    with _cliente(auth=AuthFalso(consentimientos={})) as client:
        oferta = _post(client, SOLICITAR, {"entrada": ENTRADA})["data"]["solicitarOferta"]

    assert oferta["__typename"] == "ErrorOferta"
    assert oferta["puedeReintentar"] is False
    assert oferta["motivo"] == "CONSENTIMIENTO_NO_VIGENTE"


def test_registrar_y_revocar_consentimiento() -> None:
    registrar = """mutation {
      registrarConsentimiento(entrada: {
        finalidad: "f",
        versionTexto: "v1",
        documentoIdentidad: "CC-1020304050",
        tipoDato: "FINANCIEROS_Y_ABIERTOS",
        fuenteDatos: "OPEN_FINANCE_OPEN_DATA",
        canal: "WEB"
      }) { id finalidad versionTexto vigente estado venceEn otorgadoEn revocadoEn }
    }"""
    revocar = """mutation($id: ID!) {
      revocarConsentimiento(id: $id) { id vigente estado venceEn revocadoEn }
    }"""
    from fakes import AclFalso

    auth = AuthFalso()
    acl = AclFalso()
    with _cliente(auth=auth, acl=acl) as client:
        creado = _post(client, registrar)["data"]["registrarConsentimiento"]
        revocado = _post(client, revocar, {"id": creado["id"]})["data"]["revocarConsentimiento"]

    assert creado["vigente"] is True and creado["revocadoEn"] is None
    assert creado["estado"] == "VIGENTE"
    assert creado["venceEn"].startswith("2026-11-07")
    assert creado["versionTexto"] == "v1"
    # El documento nunca llega en claro a AUTH: llega el hash opaco (`titular_id`).
    assert auth.registros_consentimiento[0]["titular_id"] is not None
    assert "1020304050" not in auth.registros_consentimiento[0]["titular_id"]
    assert revocado["vigente"] is False and revocado["revocadoEn"]
    assert revocado["estado"] == "REVOCADO"
    # CA-W01-08: revocar invalida la caché del titular en el ACL Worker.
    assert acl.invalidaciones == [auth.registros_consentimiento[0]["titular_id"]]


def test_auth_caido_da_error_publico_con_mensaje() -> None:
    query = (
        'mutation { registrarConsentimiento(entrada: {finalidad: "f", versionTexto: "v"}) {id} }'
    )
    with _cliente(auth=AuthFalso(falla=True)) as client:
        cuerpo = _post(client, query)

    assert "No pudimos registrar el consentimiento" in cuerpo["errors"][0]["message"]


def test_cobros_sin_token_exige_sesion_aun_en_modo_opcional() -> None:
    with _cliente() as client:
        cuerpo = _post(client, "{ cobros { id } }")

    assert cuerpo["errors"][0]["message"] == "Debes iniciar sesión."


def test_oferta_por_id_devuelve_null() -> None:
    with _cliente() as client:
        cuerpo = _post(client, '{ oferta(id: "o-1") { id } }')

    assert cuerpo["data"]["oferta"] is None


def test_contactar_asesor_confirma() -> None:
    query = 'mutation { contactarAsesor(ofertaId: "o-1", canal: CHAT) { canal resumen } }'
    with _cliente() as client:
        confirmacion = _post(client, query)["data"]["contactarAsesor"]

    assert confirmacion["canal"] == "CHAT"
    assert confirmacion["resumen"] == (
        "Recibimos tu solicitud de chat. Un asesor te contactará para continuar tu cotización."
    )
    # El id interno de la oferta no se le muestra al cliente.
    assert "o-1" not in confirmacion["resumen"]


def test_contactar_asesor_sin_oferta_no_deja_el_texto_a_medias() -> None:
    # Desde la pantalla de error (W01-03c) no hubo oferta: el frontend manda el id vacío.
    query = 'mutation { contactarAsesor(ofertaId: "", canal: LLAMAR) { resumen } }'
    with _cliente() as client:
        resumen = _post(client, query)["data"]["contactarAsesor"]["resumen"]

    assert resumen.startswith("Recibimos tu solicitud de llamada.")
    assert "oferta ." not in resumen


def test_errores_de_validacion_del_esquema_no_se_enmascaran() -> None:
    with _cliente() as client:
        cuerpo = _post(client, "{ campoInexistente }")

    assert "campoInexistente" in cuerpo["errors"][0]["message"]


def test_errores_internos_se_enmascaran(monkeypatch) -> None:
    rating = RatingFalso(error=RuntimeError("detalle interno secreto"))
    with _cliente(rating=rating) as client:
        cuerpo = _post(client, SOLICITAR, {"entrada": ENTRADA})

    assert cuerpo["errors"][0]["message"] == "Unexpected error."
    assert "secreto" not in str(cuerpo)
