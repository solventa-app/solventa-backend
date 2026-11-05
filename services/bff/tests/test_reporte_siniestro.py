"""Reporte de siniestro del CLIENTE por GraphQL (HU-M05, contrato v0.35): solo CLIENTE; el nombre y
el documento salen de AUTH y las reglas del caso las aplica CLAIMS."""

import pytest
from fakes import (
    AlmacenamientoFalso,
    AuthFalso,
    ClaimsFalso,
    _usuario_publico,
    clientes_falsos,
    token_de_prueba,
)
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO
from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.esquema import (
    MENSAJE_ID_REPORTE,
    MENSAJE_REPORTE_NO_DISPONIBLE,
    MENSAJE_REPORTE_SIN_IDENTIDAD,
    RAMO_POLIZA,
)
from app.main import create_app

ID = "0c9f2b7e-4a51-4c1e-9d6a-1f2e3d4c5b6a"
REPORTAR = """mutation($e: ReporteSiniestroInput!) { reportarSiniestro(entrada: $e) {
  __typename
  ... on SiniestroReportado { id radicado estado polizaId fotos venceDictamenEn }
  ... on ErrorSiniestro { mensaje } } }"""
SUBIDA = """mutation($id: ID!) { solicitarSubidaEvidenciaSiniestro(
  idReporte: $id, tipoContenido: "image/jpeg", tamanoBytes: 2048) {
  __typename ... on SubidaArchivo { url archivo { id } } ... on ErrorArchivo { codigo } } }"""
MIS = "{ misSiniestros { id radicado estado fotos } }"


def entrada(**cambios) -> dict:
    return {
        "idReporte": ID,
        "polizaId": "pol-01",
        "ocurridoEn": "2026-11-04T13:00:00+00:00",
        "descripcion": "Me chocaron por detrás en un semáforo.",
        "ubicacion": {
            "descripcion": "Cra 15 con Calle 93",
            "ciudad": "Bogotá",
            "latitud": 4.6767,
            "longitud": -74.0483,
        },
        "fotos": [
            {
                "archivoId": "arc-1",
                "tomadaEn": "2026-11-04T13:05:00+00:00",
                "latitud": 4.6767,
                "longitud": -74.0483,
            },
            {"archivoId": "arc-2", "tomadaEn": "2026-11-04T13:06:00+00:00"},
        ],
        **cambios,
    }


def _montar(verificado=True, claims=None, almacen=None, auth_falla=False):
    auth = AuthFalso(falla=auth_falla)
    auth.usuarios["c-1"] = _usuario_publico("c-1", "ana.cliente", "CLIENTE")
    auth.nombres["c-1"] = "Ana Pérez"
    if verificado:
        auth.documentos["c-1"] = "1020304050"
    claims = claims or ClaimsFalso()
    almacen = almacen or AlmacenamientoFalso()
    clientes = clientes_falsos(auth=auth, claims=claims, almacenamiento=almacen)
    return TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes)), claims


def _post(client, query, variables=None, rol="CLIENTE"):
    r = client.post(
        "/graphql/app",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id='c-1')}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def test_reporta_con_nombre_y_documento_de_auth_y_llega_pendiente():
    client, claims = _montar()
    with client:
        r = _post(client, REPORTAR, {"e": entrada()})["data"]["reportarSiniestro"]
    assert r["__typename"] == "SiniestroReportado"
    assert r["id"] == ID and r["estado"] == "PENDIENTE" and r["fotos"] == 2
    enviado = claims.llamadas[0][1]
    assert enviado["cliente_id"] == "c-1"
    assert enviado["asegurado"] == {"nombre": "Ana Pérez", "documento": "1020304050"}
    assert enviado["cobertura_aplicada"] == RAMO_POLIZA
    assert enviado["evidencias"][1]["latitud"] is None


def test_reenviar_el_mismo_reporte_devuelve_el_mismo_caso():
    client, claims = _montar()
    with client:
        primero = _post(client, REPORTAR, {"e": entrada()})["data"]["reportarSiniestro"]
        segundo = _post(client, REPORTAR, {"e": entrada()})["data"]["reportarSiniestro"]
    assert primero["radicado"] == segundo["radicado"]
    assert len(claims.siniestros) == 2  # el de la semilla del doble y el reportado


def test_sin_identidad_verificada_no_reporta():
    client, claims = _montar(verificado=False)
    with client:
        r = _post(client, REPORTAR, {"e": entrada()})["data"]["reportarSiniestro"]
    assert r == {"__typename": "ErrorSiniestro", "mensaje": MENSAJE_REPORTE_SIN_IDENTIDAD}
    assert claims.llamadas == []


def test_id_de_reporte_invalido():
    client, claims = _montar()
    with client:
        r = _post(client, REPORTAR, {"e": entrada(idReporte="no-es-uuid")})
    assert r["data"]["reportarSiniestro"]["mensaje"] == MENSAJE_ID_REPORTE
    assert claims.llamadas == []


def test_la_regla_de_claims_llega_como_mensaje_y_la_caida_como_reintento():
    claims = ClaimsFalso()
    claims.rechazo = "La póliza no existe o no es tuya."
    client, _ = _montar(claims=claims)
    with client:
        r = _post(client, REPORTAR, {"e": entrada()})["data"]["reportarSiniestro"]
    assert r["mensaje"] == "La póliza no existe o no es tuya."

    claims = ClaimsFalso()
    claims.error = ErrorServicio("caido")
    client, _ = _montar(claims=claims)
    with client:
        r = _post(client, REPORTAR, {"e": entrada()})["data"]["reportarSiniestro"]
    assert r["mensaje"] == MENSAJE_REPORTE_NO_DISPONIBLE


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
@pytest.mark.parametrize("query", [REPORTAR, SUBIDA, MIS])
def test_solo_el_cliente_reporta(rol, query):
    client, claims = _montar()
    variables = {"e": entrada(), "id": ID}
    with client:
        cuerpo = _post(client, query, variables, rol=rol)
    assert cuerpo["errors"][0]["message"] == MENSAJE_SIN_PERMISO
    assert claims.llamadas == []


def test_la_foto_se_sube_a_nombre_del_caso_no_del_usuario():
    almacen = AlmacenamientoFalso()
    client, _ = _montar(almacen=almacen)
    with client:
        r = _post(client, SUBIDA, {"id": ID.upper()})["data"]["solicitarSubidaEvidenciaSiniestro"]
    assert r["__typename"] == "SubidaArchivo"
    _, rol, actor, solicitud = almacen.llamadas[0]
    assert (rol, actor) == ("CLIENTE", "c-1")
    assert solicitud["tipo"] == "siniestro-foto"
    assert solicitud["propietario_id"] == ID  # normalizado en minúsculas


def test_subida_con_id_invalido_no_llega_al_almacenamiento():
    almacen = AlmacenamientoFalso()
    client, _ = _montar(almacen=almacen)
    with client:
        r = _post(client, SUBIDA, {"id": "otro-usuario"})["data"]
    assert r["solicitarSubidaEvidenciaSiniestro"]["codigo"] == "PROPIETARIO_INVALIDO"
    assert almacen.llamadas == []


def test_mis_siniestros_solo_trae_los_del_cliente():
    client, claims = _montar()
    with client:
        _post(client, REPORTAR, {"e": entrada()})
        lista = _post(client, MIS)["data"]["misSiniestros"]
    assert [s["id"] for s in lista] == [ID]
    assert ("de_cliente", "c-1") in claims.llamadas
