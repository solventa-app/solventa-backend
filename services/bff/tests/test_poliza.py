"""Consulta de la póliza emitida (HU-W03, KAN-212, CA-W03-07), con dobles."""

from fakes import AuthFalso, PolicyFalso, RatingFalso, clientes_falsos, token_de_prueba  # noqa: I001
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.main import create_app

POLIZA = """query($id: ID!) {
  poliza(id: $id) {
    id numero estado vigenciaDesde vigenciaHasta documentoUrl
    prima { valor moneda } cobertura { valor moneda }
  }
}"""
DATOS_POLIZA = {
    "id": "pz-1",
    "numero": "SOL-2026-000001",
    "propuesta_id": "p-1",
    "cliente_id": "cliente-opaco",
    "estado": "EMITIDA",
    "vigencia_desde": "2026-10-22T12:00:00+00:00",
    "vigencia_hasta": "2031-10-22T12:00:00+00:00",
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "actor_usuario_id": "u-1",
}


def _cliente(policy: PolicyFalso) -> TestClient:
    return TestClient(
        create_app(
            config=Configuracion.desde_entorno(),
            clientes=clientes_falsos(auth=AuthFalso(), rating=RatingFalso(), policy=policy),
        )
    )


def _consultar(client: TestClient, id_: str = "pz-1", rol: str = "CLIENTE", uid: str = "u-1"):
    r = client.post(
        "/graphql/web",
        json={"query": POLIZA, "variables": {"id": id_}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def _policy(**extra) -> PolicyFalso:
    policy = PolicyFalso(**extra)
    policy.polizas["pz-1"] = DATOS_POLIZA
    return policy


def test_el_cliente_ve_su_poliza_con_el_formato_del_contrato() -> None:
    with _cliente(_policy()) as client:
        poliza = _consultar(client)["data"]["poliza"]

    assert poliza["numero"] == "SOL-2026-000001"
    assert poliza["estado"] == "EMITIDA"
    assert poliza["prima"] == {"valor": "185000", "moneda": "COP"}
    assert poliza["cobertura"] == {"valor": "200000000", "moneda": "COP"}
    assert poliza["vigenciaHasta"].startswith("2031-10-22")
    assert poliza["documentoUrl"] == "/polizas/pz-1/documento"


def test_un_asesor_puede_ver_la_poliza() -> None:
    with _cliente(_policy()) as client:
        poliza = _consultar(client, rol="ASESOR", uid="asesor-1")["data"]["poliza"]

    assert poliza["id"] == "pz-1"


def test_un_cliente_no_ve_la_poliza_de_otro() -> None:
    with _cliente(_policy()) as client:
        assert _consultar(client, uid="u-2")["data"]["poliza"] is None


def test_una_poliza_desconocida_es_null() -> None:
    with _cliente(_policy()) as client:
        assert _consultar(client, id_="no-existe")["data"]["poliza"] is None


def test_un_backoffice_no_consulta_polizas() -> None:
    with _cliente(_policy()) as client:
        cuerpo = _consultar(client, rol="BACKOFFICE", uid="u-bo")

    assert cuerpo["data"] == {"poliza": None}  # campo nulable: null junto con el error de permiso
    assert cuerpo["errors"][0]["message"] == "No tienes permiso para esta operación."


def test_si_policy_no_responde_la_consulta_es_un_error_no_un_null() -> None:
    with _cliente(_policy(error=ErrorServicio("caído"))) as client:
        cuerpo = _consultar(client)

    assert cuerpo["data"]["poliza"] is None
    assert cuerpo["errors"][0]["message"] == "No pudimos procesar tu suscripción. Intenta de nuevo."
