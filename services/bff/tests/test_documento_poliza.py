"""Descarga del PDF de la póliza (HU-W03, KAN-215, CA-W03-08), con dobles."""

from fakes import AuthFalso, PolicyFalso, RatingFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio
from app.configuracion import Configuracion
from app.main import create_app

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
    "firmante_id": "u-1",
}


def _cliente(auth: AuthFalso | None = None, **policy_extra) -> tuple[TestClient, PolicyFalso]:
    policy = PolicyFalso(**policy_extra)
    policy.polizas["pz-1"] = DATOS_POLIZA
    clientes = clientes_falsos(auth=auth or AuthFalso(), rating=RatingFalso(), policy=policy)
    return TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes)), policy


def _pedir(client: TestClient, id_: str = "pz-1", rol: str | None = "CLIENTE", uid: str = "u-1"):
    cabeceras = {"Authorization": f"Bearer {token_de_prueba(rol, usuario_id=uid)}"} if rol else {}
    return client.get(f"/polizas/{id_}/documento", headers=cabeceras)


def test_el_cliente_descarga_su_poliza_como_pdf() -> None:
    auth = AuthFalso()
    auth.nombres["u-1"] = "Ana María Pérez"
    cliente, policy = _cliente(auth)
    with cliente as client:
        r = _pedir(client)

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="poliza-SOL-2026-000001.pdf"'
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content.startswith(b"%PDF-")
    assert policy.documentos_pedidos == [("pz-1", "Ana María Pérez")]


def test_el_nombre_del_asegurado_se_pide_a_auth_por_el_firmante() -> None:
    auth = AuthFalso()
    cliente, _ = _cliente(auth)
    with cliente as client:
        _pedir(client)

    assert auth.consultas_de_nombres == [["u-1"]]


def test_un_asesor_puede_descargarla() -> None:
    cliente, _ = _cliente()
    with cliente as client:
        assert _pedir(client, rol="ASESOR", uid="asesor-1").status_code == 200


def test_si_auth_no_responde_el_documento_sale_igual_sin_el_nombre() -> None:
    cliente, policy = _cliente(AuthFalso(falla=True))
    with cliente as client:
        r = _pedir(client)

    assert r.status_code == 200
    assert policy.documentos_pedidos == [("pz-1", None)]


def test_sin_token_responde_401_aunque_la_autenticacion_sea_opcional() -> None:
    cliente, policy = _cliente()
    with cliente as client:
        r = _pedir(client, rol=None)

    assert r.status_code == 401
    assert policy.documentos_pedidos == []  # nunca se generó el documento


def test_con_un_token_invalido_responde_401() -> None:
    cliente, _ = _cliente()
    with cliente as client:
        r = client.get("/polizas/pz-1/documento", headers={"Authorization": "Bearer basura"})

    assert r.status_code == 401


def test_un_backoffice_no_descarga_polizas() -> None:
    cliente, policy = _cliente()
    with cliente as client:
        r = _pedir(client, rol="BACKOFFICE", uid="u-bo")

    assert r.status_code == 403
    assert policy.documentos_pedidos == []


def test_un_cliente_no_descarga_la_poliza_de_otro_y_se_ve_igual_que_una_inexistente() -> None:
    cliente, policy = _cliente()
    with cliente as client:
        ajena = _pedir(client, uid="u-2")
        inexistente = _pedir(client, id_="no-existe")

    assert ajena.status_code == inexistente.status_code == 404
    assert ajena.json() == inexistente.json()
    assert policy.documentos_pedidos == []


def test_si_policy_no_responde_la_descarga_da_503_reintentable_con_mensaje() -> None:
    cliente, _ = _cliente(error=ErrorServicio("caído"))
    with cliente as client:
        r = _pedir(client)

    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
    assert r.json() == {"mensaje": "No pudimos generar el documento. Intenta de nuevo."}


def test_si_policy_falla_solo_al_generar_el_pdf_tambien_da_503() -> None:
    cliente, policy = _cliente()
    policy.error_documento = ErrorServicio("HTTP 500")
    with cliente as client:
        r = _pedir(client)

    assert r.status_code == 503
