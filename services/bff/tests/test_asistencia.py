"""Asistencia en sitio por GraphQL (HU-M06, KAN-265 y KAN-266, contrato v0.40): solo CLIENTE,
idempotente por idSolicitud, errores de negocio en la union y nunca un 5xx si CLAIMS falla."""

import asyncio

import httpx
import pytest
from fakes import ClaimsFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO
from app.clientes import AsistenciaRechazada, ClienteClaimsHttp, ErrorServicio
from app.configuracion import Configuracion
from app.esquema import MENSAJE_ASISTENCIA_NO_DISPONIBLE, MENSAJE_ID_SOLICITUD
from app.main import create_app

ID = "0b6f3a52-5d6e-4d8e-9c1a-2f4b7c9d1e00"
CAMPOS = """... on Asistencia {
  id estado tipoAsistencia minutosEstimados motivo solicitadaEn canceladaEn
  prestador { id nombre tipo } }
... on ErrorAsistencia { codigo mensaje }"""
SOLICITAR = f"""mutation($e: SolicitudAsistenciaInput!) {{
  solicitarAsistencia(entrada: $e) {{ {CAMPOS} }} }}"""
CANCELAR = f"""mutation($id: ID!) {{ cancelarAsistencia(id: $id) {{ {CAMPOS} }} }}"""


def _entrada(**cambios) -> dict:
    return {
        "idSolicitud": ID.upper(),
        "prestadorId": "prs-bog-001",
        "tipoAsistencia": "MEDICO",
        "descripcion": "Dolor en el pecho",
        "direccion": "Cra 45 # 22-18",
        "latitud": 4.676,
        "longitud": -74.048,
        **cambios,
    }


def _cliente(claims, rol="CLIENTE"):
    headers = {"Authorization": f"Bearer {token_de_prueba(rol, usuario_id='cli-1')}"} if rol else {}
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(claims=claims))
    return TestClient(app), headers


def _pedir(claims, consulta, variables, rol="CLIENTE"):
    http, headers = _cliente(claims, rol)
    with http:
        cuerpo = {"query": consulta, "variables": variables}
        r = http.post("/graphql/app", json=cuerpo, headers=headers)
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def test_solicitar_reintentar_y_cancelar():
    claims = ClaimsFalso()
    primera = _pedir(claims, SOLICITAR, {"e": _entrada()})["data"]["solicitarAsistencia"]
    assert primera["estado"] == "NO_CONFIRMADA"
    assert primera["motivo"] == "PRESTADOR_SIN_RESPUESTA"
    assert primera["minutosEstimados"] is None
    assert primera["prestador"]["nombre"] == "Clínica Norte"

    reintento = _pedir(claims, SOLICITAR, {"e": _entrada()})["data"]["solicitarAsistencia"]
    assert reintento["id"] == ID
    assert reintento["estado"] == "CONFIRMADA"
    assert reintento["minutosEstimados"] == 6

    cancelada = _pedir(claims, CANCELAR, {"id": ID})["data"]["cancelarAsistencia"]
    assert cancelada["estado"] == "CANCELADA"
    assert cancelada["canceladaEn"].startswith("2026-11-07T15:05")


def test_el_bff_manda_el_cliente_del_token_y_el_id_en_minusculas():
    claims = ClaimsFalso()
    _pedir(claims, SOLICITAR, {"e": _entrada()})
    nombre, solicitud = claims.llamadas[0]
    assert nombre == "solicitar_asistencia"
    assert solicitud == {
        "id_solicitud": ID,
        "cliente_id": "cli-1",
        "prestador_id": "prs-bog-001",
        "tipo_asistencia": "MEDICO",
        "descripcion": "Dolor en el pecho",
        "direccion": "Cra 45 # 22-18",
        "latitud": 4.676,
        "longitud": -74.048,
    }


def test_descripcion_y_direccion_son_opcionales():
    claims = ClaimsFalso()
    entrada = _entrada()
    del entrada["descripcion"], entrada["direccion"]
    assert _pedir(claims, SOLICITAR, {"e": entrada})["data"]["solicitarAsistencia"]["id"] == ID
    assert claims.llamadas[0][1]["descripcion"] is None


def test_un_id_que_no_es_uuid_no_llega_a_claims():
    claims = ClaimsFalso()
    datos = _pedir(claims, SOLICITAR, {"e": _entrada(idSolicitud="123")})["data"]
    assert datos["solicitarAsistencia"] == {
        "codigo": "DATOS_INVALIDOS",
        "mensaje": MENSAJE_ID_SOLICITUD,
    }
    assert claims.llamadas == []


@pytest.mark.parametrize("codigo", ["PRESTADOR_NO_DISPONIBLE", "TIPO_NO_ATENDIDO", "OTRO_NUEVO"])
def test_el_rechazo_de_claims_va_en_la_union(codigo):
    claims = ClaimsFalso()
    claims.rechazo_asistencia = (codigo, "Mensaje de CLAIMS.")
    error = _pedir(claims, SOLICITAR, {"e": _entrada()})["data"]["solicitarAsistencia"]
    # Un código que el BFF no conoce no rompe la union.
    esperado = codigo if codigo != "OTRO_NUEVO" else "DATOS_INVALIDOS"
    assert error == {"codigo": esperado, "mensaje": "Mensaje de CLAIMS."}


def test_cancelar_una_que_no_existe_es_no_encontrada():
    error = _pedir(ClaimsFalso(), CANCELAR, {"id": ID})["data"]["cancelarAsistencia"]
    assert error["codigo"] == "NO_ENCONTRADA"


def test_claims_caido_es_un_error_publico_con_reintento():
    claims = ClaimsFalso()
    claims.error = ErrorServicio("HTTP 503")
    cuerpo = _pedir(claims, SOLICITAR, {"e": _entrada()})
    assert cuerpo["data"] is None
    assert cuerpo["errors"][0]["message"] == MENSAJE_ASISTENCIA_NO_DISPONIBLE


def test_sin_sesion_es_sin_sesion_y_no_llama_a_claims():
    claims = ClaimsFalso()
    for consulta, variables in ((SOLICITAR, {"e": _entrada()}), (CANCELAR, {"id": ID})):
        error = _pedir(claims, consulta, variables, rol=None)["errors"][0]
        assert error["extensions"]["code"] == "SIN_SESION"
    assert claims.llamadas == []


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_solo_el_cliente(rol):
    claims = ClaimsFalso()
    cuerpo = _pedir(claims, SOLICITAR, {"e": _entrada()}, rol=rol)
    assert cuerpo["errors"][0]["message"] == MENSAJE_SIN_PERMISO
    assert claims.llamadas == []


# ---------- Cliente HTTP de CLAIMS ----------


def _http(respuesta: httpx.Response) -> ClienteClaimsHttp:
    transporte = httpx.MockTransport(lambda _peticion: respuesta)
    return ClienteClaimsHttp(httpx.AsyncClient(transport=transporte, base_url="http://claims"))


def test_el_cliente_http_convierte_409_y_404_en_rechazo_con_codigo():
    cliente = _http(httpx.Response(409, json={"detail": "No.", "codigo": "TIPO_NO_ATENDIDO"}))
    with pytest.raises(AsistenciaRechazada) as error:
        asyncio.run(cliente.solicitar_asistencia({}))
    assert (error.value.codigo, error.value.mensaje) == ("TIPO_NO_ATENDIDO", "No.")

    cliente = _http(httpx.Response(404, json={"detail": "No está.", "codigo": "NO_ENCONTRADA"}))
    with pytest.raises(AsistenciaRechazada) as error:
        asyncio.run(cliente.cancelar_asistencia("a-1", "cli-1"))
    assert error.value.codigo == "NO_ENCONTRADA"


def test_el_cliente_http_trata_503_y_422_como_corresponde():
    with pytest.raises(ErrorServicio):
        asyncio.run(_http(httpx.Response(503, json={"detail": "x"})).solicitar_asistencia({}))
    with pytest.raises(AsistenciaRechazada) as error:
        asyncio.run(_http(httpx.Response(422, json={"detail": []})).solicitar_asistencia({}))
    assert error.value.codigo == "DATOS_INVALIDOS"
