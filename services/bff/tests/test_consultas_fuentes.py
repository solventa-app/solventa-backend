"""Historial de fuentes consultadas (QA 2026-11-10, Q-08, contrato v0.45): cada cotización deja su
renglón en RISK; solo el ASESOR que cotizó lo ve; y nunca un 5xx ni datos personales."""

from dataclasses import replace

from fakes import OFERTA_PRELIMINAR, RatingFalso, RiskFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO, MENSAJE_SIN_SESION
from app.configuracion import Configuracion
from app.identificadores import enmascarar_documento
from app.main import create_app

DOCUMENTO = "1020304050"

SOLICITAR = """
mutation($entrada: SolicitudOfertaInput!) {
  solicitarOferta(entrada: $entrada) { __typename ... on ErrorOferta { mensaje } }
}
"""
ENTRADA = {
    "consentimientoId": "c-1",
    "documentoIdentidad": DOCUMENTO,
    "valorCredito": "200000000",
    "plazoMeses": 60,
    "direccionInmueble": "Calle 1 # 2-3",
}
LISTA = """
query($p: Int, $t: Int) {
  consultasFuentes(pagina: $p, tamano: $t) {
    total pagina tamano
    consultas {
      id creadaEn documentoEnmascarado tipoOferta producto ofertaId
      fuentes { id nombre origen estado capturadoEn }
    }
  }
}
"""
DETALLE = """
query($id: ID!) {
  consultaFuentes(id: $id) { id documentoEnmascarado tipoOferta fuentes { id estado capturadoEn } }
}
"""


def _cliente(risk: RiskFalso, modo: str = "obligatoria", **fakes) -> TestClient:
    config = replace(Configuracion.desde_entorno(), autenticacion_usuarios=modo)
    return TestClient(create_app(config=config, clientes=clientes_falsos(risk=risk, **fakes)))


def _post(
    client: TestClient,
    query: str,
    variables: dict | None = None,
    rol: str | None = None,
    usuario: str = "u-1",
):
    cabeceras = {"Authorization": f"Bearer {token_de_prueba(rol, usuario)}"} if rol else {}
    r = client.post(
        "/graphql/web", json={"query": query, "variables": variables or {}}, headers=cabeceras
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _cotizar(client: TestClient, rol: str = "ASESOR", usuario: str = "asesor-1") -> dict:
    return _post(client, SOLICITAR, {"entrada": ENTRADA}, rol, usuario)["data"]["solicitarOferta"]


def test_cotizar_deja_el_renglon_en_risk_con_el_estado_de_las_fuentes() -> None:
    risk = RiskFalso()
    with _cliente(risk) as client:
        assert _cotizar(client)["__typename"] == "Oferta"

    (consulta,) = risk.consultas
    assert consulta["actor_id"] == "asesor-1" and consulta["actor_rol"] == "ASESOR"
    assert consulta["tipo_oferta"] == "COMPLETA" and consulta["producto"] == "VIDA_DEUDOR"
    assert consulta["oferta_id"] == "o-1" and consulta["consentimiento_id"] == "c-1"
    assert consulta["fuentes"] == [
        {
            "id": "historial-crediticio",
            "nombre": "historial-crediticio",
            "origen": "OPEN_FINANCE",
            "estado": "OK",
            "capturado_en": None,
        }
    ]


def test_el_renglon_no_lleva_el_documento_en_claro_ni_datos_de_las_fuentes() -> None:
    risk = RiskFalso()
    with _cliente(risk) as client:
        _cotizar(client)

    texto = str(risk.consultas[0])
    assert DOCUMENTO not in texto
    assert consulta_enmascarada(risk) == "10••••4050"
    assert "titular_id" in risk.consultas[0] and risk.consultas[0]["titular_id"] != DOCUMENTO


def consulta_enmascarada(risk: RiskFalso) -> str:
    return risk.consultas[0]["documento_enmascarado"]


def test_si_risk_no_registra_la_consulta_la_oferta_sigue() -> None:
    risk = RiskFalso()
    risk.falla_consultas = True
    with _cliente(risk) as client:
        assert _cotizar(client)["__typename"] == "Oferta"
    assert risk.consultas == []


def test_el_asesor_ve_su_historial_con_el_estado_de_cada_fuente() -> None:
    risk = RiskFalso()
    with _cliente(risk, rating=RatingFalso(oferta=OFERTA_PRELIMINAR)) as client:
        _cotizar(client)
        cuerpo = _post(client, LISTA, {"p": 1, "t": 10}, "ASESOR", "asesor-1")

    pagina = cuerpo["data"]["consultasFuentes"]
    assert pagina["total"] == 1 and pagina["pagina"] == 1 and pagina["tamano"] == 10
    (consulta,) = pagina["consultas"]
    assert consulta["documentoEnmascarado"] == "10••••4050"
    assert consulta["tipoOferta"] == "PRELIMINAR" and consulta["producto"] == "VIDA_DEUDOR"
    assert consulta["fuentes"][0]["estado"] == "CACHE"
    assert consulta["fuentes"][0]["capturadoEn"].startswith("2026-10-08T11:55:00")


def test_un_asesor_no_ve_lo_de_otro_y_la_ajena_responde_null() -> None:
    risk = RiskFalso()
    with _cliente(risk) as client:
        _cotizar(client, usuario="asesor-1")
        propia = _post(client, LISTA, {}, "ASESOR", "asesor-1")["data"]["consultasFuentes"]
        ajena = _post(client, LISTA, {}, "ASESOR", "asesor-2")["data"]["consultasFuentes"]
        id_consulta = propia["consultas"][0]["id"]
        detalle_ajeno = _post(client, DETALLE, {"id": id_consulta}, "ASESOR", "asesor-2")
        detalle_propio = _post(client, DETALLE, {"id": id_consulta}, "ASESOR", "asesor-1")

    assert propia["total"] == 1
    assert ajena == {"total": 0, "pagina": 1, "tamano": 10, "consultas": []}
    assert detalle_ajeno["data"]["consultaFuentes"] is None
    assert detalle_propio["data"]["consultaFuentes"]["id"] == id_consulta


def test_pagina_de_10_en_10_de_la_mas_reciente_a_la_mas_antigua() -> None:
    risk = RiskFalso()
    with _cliente(risk) as client:
        for _ in range(12):
            _cotizar(client)
        primera = _post(client, LISTA, {"p": 1, "t": 10}, "ASESOR", "asesor-1")["data"]
        segunda = _post(client, LISTA, {"p": 2, "t": 10}, "ASESOR", "asesor-1")["data"]
        enorme = _post(client, LISTA, {"p": 1, "t": 100000}, "ASESOR", "asesor-1")["data"]

    assert primera["consultasFuentes"]["total"] == 12
    assert len(primera["consultasFuentes"]["consultas"]) == 10
    assert len(segunda["consultasFuentes"]["consultas"]) == 2
    assert primera["consultasFuentes"]["consultas"][0]["id"] == "c-12"
    assert enorme["consultasFuentes"]["tamano"] == 50


def test_un_cliente_no_ve_el_historial_pero_su_cotizacion_funciona() -> None:
    risk = RiskFalso()
    with _cliente(risk) as client:
        assert _cotizar(client, "CLIENTE", "cliente-1")["__typename"] == "Oferta"
        lista = _post(client, LISTA, {}, "CLIENTE", "cliente-1")
        detalle = _post(client, DETALLE, {"id": "c-1"}, "CLIENTE", "cliente-1")

    assert lista["errors"][0]["message"] == MENSAJE_SIN_PERMISO
    assert detalle["errors"][0]["message"] == MENSAJE_SIN_PERMISO
    assert lista["data"] is None


def test_el_operador_tampoco_lo_ve() -> None:
    with _cliente(RiskFalso()) as client:
        lista = _post(client, LISTA, {}, "BACKOFFICE", "op-1")
    assert lista["errors"][0]["message"] == MENSAJE_SIN_PERMISO


def test_sin_sesion_no_hay_historial_ni_en_modo_opcional() -> None:
    with _cliente(RiskFalso(), modo="opcional") as client:
        lista = _post(client, LISTA, {})
        detalle = _post(client, DETALLE, {"id": "c-1"})
    assert lista["errors"][0]["message"] == MENSAJE_SIN_SESION
    assert detalle["errors"][0]["message"] == MENSAJE_SIN_SESION


def test_sin_sesion_cotizar_no_deja_renglon() -> None:
    """En modo `opcional` hay cotizaciones anónimas (pruebas locales): sin actor no se registra."""
    risk = RiskFalso()
    with _cliente(risk, modo="opcional") as client:
        cuerpo = _post(client, SOLICITAR, {"entrada": ENTRADA})
    assert cuerpo["data"]["solicitarOferta"]["__typename"] == "Oferta"
    assert risk.consultas == []


def test_si_risk_no_responde_el_historial_da_un_error_claro_y_no_un_5xx() -> None:
    risk = RiskFalso()
    with _cliente(risk) as client:
        _cotizar(client)
        risk.falla_consultas = True
        lista = _post(client, LISTA, {}, "ASESOR", "asesor-1")
        detalle = _post(client, DETALLE, {"id": "c-1"}, "ASESOR", "asesor-1")

    for cuerpo in (lista, detalle):
        mensaje = cuerpo["errors"][0]["message"]
        assert mensaje == "No pudimos cargar tus consultas. Intenta nuevamente."
        assert "503" not in mensaje


def test_enmascarar_documento_conserva_la_longitud_y_quita_los_separadores() -> None:
    assert enmascarar_documento("1020304050") == "10••••4050"
    assert enmascarar_documento("1.020.304.050") == "10••••4050"
    assert enmascarar_documento("123456") == "••3456"
    assert enmascarar_documento("1234") == "1234"
