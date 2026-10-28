"""Mesa de siniestros por GraphQL (HU-W04, contrato v0.21): solo BACKOFFICE; la póliza la resuelve
POLICY y la URL de cada evidencia el almacenamiento, como el BACKOFFICE que consulta."""

import pytest
from fakes import (
    AlmacenamientoFalso,
    ClaimsFalso,
    PolicyFalso,
    clientes_falsos,
    token_de_prueba,
)
from fastapi.testclient import TestClient

from app.autenticacion_usuarios import MENSAJE_SIN_PERMISO
from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.esquema import RAMO_POLIZA
from app.main import create_app

BANDEJA = "{ siniestros { id radicado estado asegurado { nombre } notificadoEn } }"
DETALLE = """query($id: ID!) { siniestro(id: $id) {
  id radicado estado asegurado { nombre documento }
  poliza { id numero ramo cobertura { valor moneda } }
  evidencias { id titulo archivo url geoetiquetada }
  verificaciones { descripcion cumple }
  resolucion { tipo }
} }"""
PERITOS = (
    'query { peritos(siniestroId: "sin-01") { id nombre tiempoRespuestaDias vencimientoInforme } }'
)
RESULTADO = """__typename
  ... on Siniestro { id estado resolucion { tipo motivo comentario indemnizacion { valor moneda }
                     perito { nombre } instrucciones } }
  ... on ErrorSiniestro { mensaje }"""
APROBAR = f'mutation {{ aprobarSiniestro(id: "sin-01") {{ {RESULTADO} }} }}'
RECHAZAR = (
    'mutation { rechazarSiniestro(id: "sin-01", motivo: OTRO, comentario: "No aplica") '
    f"{{ {RESULTADO} }} }}"
)
DERIVAR = (
    'mutation { derivarSiniestro(id: "sin-01", peritoId: "per-01", instrucciones: "Revisar") '
    f"{{ {RESULTADO} }} }}"
)

POLIZA = {
    "id": "pol-01",
    "numero": "SOL-2026-000001",
    "estado": "EMITIDA",
    "vigencia_desde": "2026-02-10T05:00:00+00:00",
    "vigencia_hasta": "2027-02-10T05:00:00+00:00",
    "prima": {"valor": "1560000", "moneda": "COP"},
    "cobertura": {"valor": "52000000", "moneda": "COP"},
}


def _clientes(claims=None, almacen=None, policy=None):
    policy = policy or PolicyFalso()
    policy.polizas["pol-01"] = POLIZA
    almacen = almacen or AlmacenamientoFalso()
    almacen.archivos["arch-1"] = {
        "id": "arch-1",
        "estado": "DISPONIBLE",
        "propietario_id": "sin-01",
    }
    return clientes_falsos(claims=claims or ClaimsFalso(), almacenamiento=almacen, policy=policy)


def _post(client, query, variables=None, rol="BACKOFFICE", ruta="/graphql/web"):
    r = client.post(
        ruta,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id='op-1')}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _cliente(clientes):
    return TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes))


@pytest.mark.parametrize("rol", ["CLIENTE", "ASESOR"])
@pytest.mark.parametrize("query", [BANDEJA, PERITOS, APROBAR, RECHAZAR, DERIVAR])
def test_solo_el_backoffice_usa_la_mesa(rol, query):
    claims = ClaimsFalso()
    with _cliente(_clientes(claims)) as client:
        cuerpo = _post(client, query, rol=rol)
    assert cuerpo["errors"][0]["message"] == MENSAJE_SIN_PERMISO
    assert claims.llamadas == []


def test_bandeja_no_pide_polizas_ni_urls_si_no_las_usa():
    almacen, policy = AlmacenamientoFalso(), PolicyFalso()
    with _cliente(_clientes(almacen=almacen, policy=policy)) as client:
        datos = _post(client, BANDEJA)["data"]["siniestros"]
    assert datos == [
        {
            "id": "sin-01",
            "radicado": "SIN-2026-0141",
            "estado": "PENDIENTE",
            "asegurado": {"nombre": "Camilo Rojas"},
            "notificadoEn": "2026-10-19T16:15:00+00:00",
        }
    ]
    assert almacen.llamadas == []


def test_detalle_con_poliza_ramo_y_url_firmada_como_el_operador():
    almacen = AlmacenamientoFalso()
    with _cliente(_clientes(almacen=almacen)) as client:
        s = _post(client, DETALLE, {"id": "sin-01"})["data"]["siniestro"]
    assert s["poliza"] == {
        "id": "pol-01",
        "numero": "SOL-2026-000001",
        "ramo": RAMO_POLIZA,
        "cobertura": {"valor": "52000000", "moneda": "COP"},
    }
    assert s["evidencias"][0]["url"].startswith("https://almacen.prueba/arch-1")
    assert s["evidencias"][0]["geoetiquetada"] is True
    # La URL se pidió a nombre del BACKOFFICE que consulta (queda auditada en el almacenamiento).
    assert almacen.llamadas == [("descarga", "BACKOFFICE", "op-1", "arch-1")]


def test_evidencia_sin_url_si_el_almacenamiento_falla():
    almacen = AlmacenamientoFalso()
    almacen.error = ErrorServicio("caido")
    with _cliente(_clientes(almacen=almacen)) as client:
        s = _post(client, DETALLE, {"id": "sin-01"})["data"]["siniestro"]
    assert s["evidencias"][0]["url"] == ""


def test_siniestro_inexistente_es_null():
    with _cliente(_clientes()) as client:
        assert _post(client, DETALLE, {"id": "sin-99"})["data"]["siniestro"] is None


def test_claims_caido_es_un_error_publico_sin_5xx():
    claims = ClaimsFalso()
    claims.error = ErrorServicio("timeout")
    with _cliente(_clientes(claims)) as client:
        cuerpo = _post(client, BANDEJA)
    assert (
        cuerpo["errors"][0]["message"] == "No pudimos consultar los siniestros. Intenta de nuevo."
    )


def test_peritos():
    with _cliente(_clientes()) as client:
        peritos = _post(client, PERITOS)["data"]["peritos"]
    assert peritos[0]["nombre"] == "Peritajes Andes Ltda."
    assert peritos[0]["vencimientoInforme"] == "2026-11-03T17:00:00-05:00"


def test_aprobar_manda_el_actor_y_devuelve_el_siniestro():
    claims = ClaimsFalso()
    with _cliente(_clientes(claims)) as client:
        r = _post(client, APROBAR)["data"]["aprobarSiniestro"]
    assert r["__typename"] == "Siniestro" and r["estado"] == "APROBADO"
    assert r["resolucion"]["indemnizacion"] == {"valor": "8650000", "moneda": "COP"}
    assert claims.llamadas[-1] == ("aprobar", "sin-01", "op-1")


def test_rechazar_y_derivar():
    claims = ClaimsFalso()
    with _cliente(_clientes(claims)) as client:
        d = _post(client, DERIVAR)["data"]["derivarSiniestro"]
        assert d["estado"] == "EN_REVISION" and d["resolucion"]["perito"]["nombre"]
        r = _post(client, RECHAZAR)["data"]["rechazarSiniestro"]
    assert r["resolucion"]["motivo"] == "OTRO" and r["resolucion"]["comentario"] == "No aplica"
    assert claims.llamadas[-1] == ("rechazar", "sin-01", "OTRO", "No aplica", "op-1")


def test_error_de_negocio_llega_como_error_siniestro_con_el_mensaje_de_claims():
    claims = ClaimsFalso()
    claims.rechazo = "El siniestro ya fue resuelto y no admite cambios."
    with _cliente(_clientes(claims)) as client:
        r = _post(client, APROBAR)["data"]["aprobarSiniestro"]
    assert r == {"__typename": "ErrorSiniestro", "mensaje": claims.rechazo}


def test_falla_tecnica_al_resolver_es_error_siniestro_con_reintento():
    claims = ClaimsFalso()
    claims.error = ErrorServicio("timeout")
    with _cliente(_clientes(claims)) as client:
        r = _post(client, DERIVAR)["data"]["derivarSiniestro"]
    assert r["__typename"] == "ErrorSiniestro"
    assert "Intenta de nuevo" in r["mensaje"]


def test_v1_responde_lo_mismo_que_la_ruta_sin_version():
    with _cliente(_clientes()) as client:
        sin_version = _post(client, BANDEJA)
        v1 = _post(client, BANDEJA, ruta="/v1/graphql/web")
    assert v1 == sin_version


def test_mensajes_en_ingles():
    claims = ClaimsFalso()
    claims.rechazo = "El siniestro ya fue resuelto y no admite cambios."
    with _cliente(_clientes(claims)) as client:
        r = client.post(
            "/graphql/web",
            json={"query": APROBAR},
            headers={
                "Authorization": f"Bearer {token_de_prueba('BACKOFFICE')}",
                "Accept-Language": "en",
            },
        ).json()
    assert r["data"]["aprobarSiniestro"]["mensaje"] == (
        "The claim has already been resolved and can't be changed."
    )
