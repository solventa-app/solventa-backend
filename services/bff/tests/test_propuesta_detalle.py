"""`propuesta(id)` con lo mismo que trae la lista (QA 2026-11-10, Q-09, detalle de la póliza G-06b):
la póliza cuando ya se emitió y, solo para el ASESOR, el nombre del titular. Con dobles, sin red."""

from fakes import AuthFalso, PolicyFalso, RatingFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.main import create_app

PROPUESTA = """query($id: ID!) {
  propuesta(id: $id) {
    id estado estadoVisible polizaId nombreTitular
    prima { valor moneda } cobertura { valor moneda }
    poliza { id numero estado vigenciaDesde vigenciaHasta documentoUrl prima { valor } }
  }
}"""
EMITIDA = {
    "id": "prop-1",
    "oferta_id": "o-1",
    "cliente_id": "cliente-opaco",
    "actor_usuario_id": "u-1",
    "actor_rol": "CLIENTE",
    "estado": "EMITIDA",
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "plazo_meses": 60,
    "poliza_id": "pz-1",
    "actualizado_en": "2026-10-22T12:00:00+00:00",
}
POLIZA = {
    "id": "pz-1",
    "numero": "SOL-2026-000001",
    "propuesta_id": "prop-1",
    "cliente_id": "cliente-opaco",
    "estado": "EMITIDA",
    "vigencia_desde": "2026-10-22T12:00:00+00:00",
    "vigencia_hasta": "2031-10-22T12:00:00+00:00",
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "actor_usuario_id": "u-1",
}


def _cliente(policy: PolicyFalso) -> TestClient:
    clientes = clientes_falsos(auth=AuthFalso(), rating=RatingFalso(), policy=policy)
    return TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes))


def _policy(propuesta: dict | None = None, **extra) -> PolicyFalso:
    policy = PolicyFalso(**extra)
    policy.propuestas["o-1"] = propuesta or EMITIDA
    policy.polizas["pz-1"] = POLIZA
    policy.nombres = {"prop-1": "Ana Pérez"}
    return policy


def _post(client: TestClient, rol: str = "CLIENTE", uid: str = "u-1", id_: str = "prop-1"):
    r = client.post(
        "/graphql/web",
        json={"query": PROPUESTA, "variables": {"id": id_}},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"},
    )
    assert r.status_code == 200, r.text  # nunca un 5xx
    return r.json()


def test_el_cliente_ve_la_poliza_emitida_y_no_el_nombre_del_titular() -> None:
    with _cliente(_policy()) as client:
        propuesta = _post(client)["data"]["propuesta"]

    assert propuesta["estadoVisible"] == "EMITIDA" and propuesta["polizaId"] == "pz-1"
    assert propuesta["nombreTitular"] is None
    poliza = propuesta["poliza"]
    assert (
        poliza["numero"] == "SOL-2026-000001"
        and poliza["documentoUrl"] == "/polizas/pz-1/documento"
    )
    assert poliza["vigenciaHasta"].startswith("2031-10-22")


def test_el_asesor_ve_ademas_el_nombre_del_titular() -> None:
    policy = _policy({**EMITIDA, "actor_usuario_id": "asesor-1", "actor_rol": "ASESOR"})
    policy.polizas["pz-1"] = {**POLIZA, "actor_usuario_id": "asesor-1"}
    with _cliente(policy) as client:
        propuesta = _post(client, rol="ASESOR", uid="asesor-1")["data"]["propuesta"]

    assert propuesta["nombreTitular"] == "Ana Pérez"
    assert propuesta["poliza"]["id"] == "pz-1"


def test_una_propuesta_sin_emitir_no_lleva_poliza() -> None:
    policy = _policy({**EMITIDA, "estado": "EN_FIRMA", "poliza_id": None})
    with _cliente(policy) as client:
        propuesta = _post(client)["data"]["propuesta"]

    assert propuesta["estadoVisible"] == "EMITIENDO" and propuesta["poliza"] is None


def test_si_policy_no_entrega_la_poliza_la_propuesta_sale_igual() -> None:
    policy = _policy()
    del policy.polizas["pz-1"]
    with _cliente(policy) as client:
        cuerpo = _post(client)

    assert "errors" not in cuerpo and cuerpo["data"]["propuesta"]["id"] == "prop-1"
    assert cuerpo["data"]["propuesta"]["poliza"] is None


def test_si_no_hay_nombres_la_propuesta_del_asesor_sale_sin_titular() -> None:
    policy = _policy({**EMITIDA, "actor_usuario_id": "asesor-1", "actor_rol": "ASESOR"})
    policy.polizas["pz-1"] = {**POLIZA, "actor_usuario_id": "asesor-1"}
    policy.falla_nombres = True
    with _cliente(policy) as client:
        cuerpo = _post(client, rol="ASESOR", uid="asesor-1")

    assert "errors" not in cuerpo
    assert cuerpo["data"]["propuesta"]["nombreTitular"] is None
    assert cuerpo["data"]["propuesta"]["poliza"]["id"] == "pz-1"


def test_la_poliza_de_otro_cliente_no_se_filtra_por_la_propuesta() -> None:
    with _cliente(_policy()) as client:
        ajena = _post(client, uid="u-2")["data"]["propuesta"]

    assert ajena is None


def test_si_la_poliza_falla_con_error_de_servicio_no_rompe_la_propuesta() -> None:
    class PolicyConPolizaCaida(PolicyFalso):
        async def obtener_poliza(self, poliza_id: str):  # type: ignore[override]
            raise ErrorServicio("caída")

    policy = PolicyConPolizaCaida()
    policy.propuestas["o-1"] = EMITIDA
    with _cliente(policy) as client:
        cuerpo = _post(client)

    assert "errors" not in cuerpo and cuerpo["data"]["propuesta"]["poliza"] is None
