"""El dummy de siniestros: consultas, reglas de las acciones, imágenes y reinicio de datos."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositorio import Repositorio, sumar_dias_habiles

GRAPHQL = "/graphql/siniestros"
AHORA = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)  # jueves

SINIESTRO = """
  query ($id: ID!) {
    siniestro(id: $id) {
      id radicado estado
      asegurado { nombre documento }
      poliza { numero estado ramo vigenciaDesde prima { valor moneda } }
      sumaReclamada { valor moneda }
      venceDictamenEn
      evidencias { tipo archivo url geoetiquetada }
      verificaciones { descripcion cumple }
      resolucion {
        tipo motivo comentario perito { nombre } vencimientoInforme indemnizacion { valor }
      }
    }
  }
"""

RESOLUCION = """
  ... on Siniestro { id estado resolucion { tipo motivo comentario instrucciones perito { id } } }
  ... on ErrorSiniestro { mensaje }
"""


@pytest.fixture
def client():
    with TestClient(create_app(Repositorio(reloj=lambda: AHORA))) as c:
        yield c


def gql(client, query, **variables):
    r = client.post(GRAPHQL, json={"query": query, "variables": variables})
    assert r.status_code == 200
    cuerpo = r.json()
    assert "errors" not in cuerpo, cuerpo
    return cuerpo["data"]


def test_lista_los_10_siniestros_del_mas_reciente_al_mas_antiguo(client):
    datos = gql(client, "{ siniestros { radicado estado notificadoEn } }")["siniestros"]
    assert len(datos) == 10
    fechas = [s["notificadoEn"] for s in datos]
    assert fechas == sorted(fechas, reverse=True)


def test_filtra_por_estado(client):
    datos = gql(client, "{ siniestros(estado: EN_REVISION) { estado } }")["siniestros"]
    assert datos and {s["estado"] for s in datos} == {"EN_REVISION"}


def test_detalle_con_poliza_en_el_formato_del_servicio_de_polizas(client):
    s = gql(client, SINIESTRO, id="sin-01")["siniestro"]
    assert s["radicado"] == "SIN-2026-0141"
    assert s["poliza"]["numero"] == "SOL-2026-048217"
    assert s["poliza"]["estado"] == "EMITIDA"
    assert s["sumaReclamada"] == {"valor": "8650000", "moneda": "COP"}
    assert all(e["url"].startswith("/graphql/siniestros/medios/") for e in s["evidencias"])


def test_el_vencimiento_del_dictamen_es_relativo_al_momento_de_carga(client):
    s = gql(client, SINIESTRO, id="sin-01")["siniestro"]
    assert datetime.fromisoformat(s["venceDictamenEn"]) > AHORA


def test_siniestro_inexistente_es_null(client):
    assert gql(client, SINIESTRO, id="sin-99")["siniestro"] is None


def test_sirve_las_imagenes_de_evidencia(client):
    s = gql(client, SINIESTRO, id="sin-01")["siniestro"]
    for e in s["evidencias"]:
        r = client.get(e["url"])
        assert r.status_code == 200
        assert r.headers["content-type"] in ("image/jpeg", "image/png")


def test_peritos_traen_el_vencimiento_legal_del_informe(client):
    peritos = gql(
        client, '{ peritos(siniestroId: "sin-01") { id tiempoRespuestaDias vencimientoInforme } }'
    )["peritos"]
    assert len(peritos) == 4
    assert all(p["vencimientoInforme"] for p in peritos)


def test_sumar_dias_habiles_salta_fines_de_semana_y_vence_a_las_5_pm_hora_colombia():
    vence = sumar_dias_habiles(AHORA, 2)  # jueves + 2 hábiles = lunes
    assert vence.weekday() == 0
    assert (vence.hour, vence.minute) == (17, 0)
    assert vence.utcoffset().total_seconds() == -5 * 3600


def test_aprobar_cambia_el_estado_y_queda_en_la_bandeja(client):
    r = gql(client, f'mutation {{ aprobarSiniestro(id: "sin-01") {{ {RESOLUCION} }} }}')
    assert r["aprobarSiniestro"]["estado"] == "APROBADO"
    s = gql(client, SINIESTRO, id="sin-01")["siniestro"]
    assert s["estado"] == "APROBADO"
    assert s["resolucion"]["indemnizacion"]["valor"] == "8650000"


def test_rechazar_guarda_motivo_y_comentario(client):
    r = gql(
        client,
        f'mutation {{ rechazarSiniestro(id: "sin-10", motivo: EVIDENCIA_INSUFICIENTE, '
        f'comentario: "Falta la foto del parqueadero") {{ {RESOLUCION} }} }}',
    )["rechazarSiniestro"]
    assert r["estado"] == "RECHAZADO"
    assert r["resolucion"]["motivo"] == "EVIDENCIA_INSUFICIENTE"
    assert r["resolucion"]["comentario"] == "Falta la foto del parqueadero"


def test_rechazar_sin_motivo_es_un_error_de_esquema(client):
    r = client.post(
        GRAPHQL, json={"query": 'mutation { rechazarSiniestro(id: "sin-10") { __typename } }'}
    )
    assert r.json().get("errors")


def test_un_comentario_de_mas_de_500_caracteres_se_rechaza(client):
    r = client.post(
        GRAPHQL,
        json={
            "query": 'mutation ($c: String) { rechazarSiniestro(id: "sin-10", '
            f"motivo: OTRO, comentario: $c) {{ {RESOLUCION} }} }}",
            "variables": {"c": "x" * 501},
        },
    ).json()["data"]["rechazarSiniestro"]
    assert r == {"mensaje": "El texto supera los 500 caracteres."}


def test_derivar_deja_el_siniestro_en_revision_con_perito(client):
    r = gql(
        client,
        f'mutation {{ derivarSiniestro(id: "sin-03", peritoId: "per-03", '
        f'instrucciones: "Revisar la suspensión") {{ {RESOLUCION} }} }}',
    )["derivarSiniestro"]
    assert r["estado"] == "EN_REVISION"
    assert r["resolucion"]["tipo"] == "DERIVACION"
    assert r["resolucion"]["perito"]["id"] == "per-03"


def test_no_se_deriva_dos_veces_ni_se_resuelve_un_siniestro_cerrado(client):
    derivar = f'mutation {{ derivarSiniestro(id: "sin-04", peritoId: "per-01") {{{RESOLUCION}}} }}'
    derivado = gql(client, derivar)["derivarSiniestro"]
    assert derivado == {"mensaje": "El siniestro ya tiene un perito asignado."}
    cerrado = gql(client, f'mutation {{ aprobarSiniestro(id: "sin-05") {{ {RESOLUCION} }} }}')
    assert cerrado["aprobarSiniestro"] == {
        "mensaje": "El siniestro ya fue resuelto y no admite cambios."
    }


def test_un_siniestro_derivado_se_puede_aprobar_y_conserva_el_perito(client):
    r = gql(client, f'mutation {{ aprobarSiniestro(id: "sin-04") {{ {RESOLUCION} }} }}')
    assert r["aprobarSiniestro"]["estado"] == "APROBADO"
    assert r["aprobarSiniestro"]["resolucion"]["perito"]["id"] == "per-04"


def test_perito_inexistente_es_un_error_de_negocio(client):
    derivar = f'mutation {{ derivarSiniestro(id: "sin-03", peritoId: "per-99") {{{RESOLUCION}}} }}'
    r = gql(client, derivar)
    assert r["derivarSiniestro"] == {"mensaje": "El perito seleccionado no está disponible."}


def test_reiniciar_vuelve_a_los_datos_originales(client):
    gql(client, f'mutation {{ aprobarSiniestro(id: "sin-01") {{ {RESOLUCION} }} }}')
    r = client.post(f"{GRAPHQL}/control/reiniciar")
    assert r.json() == {"siniestros": 10}
    assert gql(client, SINIESTRO, id="sin-01")["siniestro"]["estado"] == "PENDIENTE"
