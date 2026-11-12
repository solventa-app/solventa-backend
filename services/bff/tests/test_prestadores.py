"""Catálogo maestro de prestadores por GraphQL (HU-M06, KAN-263, contrato v0.36): sesión de
CLIENTE, versionado y sin 5xx si CLAIMS falla."""

import pytest
from fakes import ClaimsFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO
from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.esquema import MENSAJE_PRESTADORES
from app.main import create_app

CATALOGO = """query($v: String) { catalogoPrestadores(versionLocal: $v) {
  version vigente prestadores {
    id nombre tipo ciudad latitud longitud activo aliado especialidad direccion horario fotoUrl
  }
} }"""


def _post(claims, variables=None, rol: str | None = "CLIENTE", ruta="/graphql/app"):
    headers = {"Authorization": f"Bearer {token_de_prueba(rol, usuario_id='cli-1')}"} if rol else {}
    app = create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(claims=claims))
    with TestClient(app) as client:
        r = client.post(
            ruta, json={"query": CATALOGO, "variables": variables or {}}, headers=headers
        )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


@pytest.mark.parametrize("variables", [{}, {"v": "2026-10-01.1"}])
def test_sin_version_o_con_una_vieja_trae_el_catalogo(variables):
    claims = ClaimsFalso()
    datos = _post(claims, variables)["data"]["catalogoPrestadores"]
    assert datos["version"] == "2026-11-04.1"
    assert datos["vigente"] is False
    # El tipo que el BFF no conoce se omite; el inactivo viaja para que la app lo descarte.
    # v0.49: GRUA ya es un tipo conocido.
    assert [(p["id"], p["tipo"], p["activo"]) for p in datos["prestadores"]] == [
        ("prs-bog-001", "MEDICO", True),
        ("prs-bog-002", "MECANICO", False),
        ("prs-bog-099", "GRUA", True),
    ]
    clinica, taller, grua = datos["prestadores"]
    assert clinica["latitud"] == 4.6859
    assert clinica["aliado"] is True
    assert clinica["especialidad"] == "Urgencias"
    assert clinica["horario"] == "Abierto 24 h"
    assert clinica["fotoUrl"] is None
    assert grua["fotoUrl"] == "https://example.test/grua.webp"
    # Un CLAIMS anterior a la v0.49 no rompe la respuesta: valores por defecto.
    assert (taller["aliado"], taller["especialidad"], taller["direccion"]) == (False, "", "")
    assert claims.llamadas == [("catalogo_prestadores", variables.get("v"))]


def test_con_la_version_vigente_viaja_vacio():
    datos = _post(ClaimsFalso(), {"v": "2026-11-04.1"})["data"]["catalogoPrestadores"]
    assert datos == {"version": "2026-11-04.1", "vigente": True, "prestadores": []}


def test_sin_sesion_es_sin_sesion_y_no_llama_a_claims():
    claims = ClaimsFalso()
    error = _post(claims, rol=None)["errors"][0]
    assert error["extensions"]["code"] == "SIN_SESION"
    assert claims.llamadas == []


@pytest.mark.parametrize("rol", ["ASESOR", "BACKOFFICE"])
def test_solo_el_cliente(rol):
    claims = ClaimsFalso()
    assert _post(claims, rol=rol)["errors"][0]["message"] == MENSAJE_SIN_PERMISO
    assert claims.llamadas == []


def test_claims_caido_es_un_error_publico_con_reintento():
    claims = ClaimsFalso()
    claims.error = ErrorServicio("HTTP 503")
    cuerpo = _post(claims)
    assert cuerpo["data"] is None
    assert cuerpo["errors"][0]["message"] == MENSAJE_PRESTADORES


def test_tambien_por_la_ruta_versionada():
    datos = _post(ClaimsFalso(), ruta="/v1/graphql/app")["data"]["catalogoPrestadores"]
    assert datos["vigente"] is False
