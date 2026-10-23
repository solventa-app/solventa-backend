"""Endpoints de POLICY con dobles de prueba (sin PostgreSQL ni RATING reales: la verificación con
los reales se hace en vivo con `docker compose`)."""

from fakes import (
    AclFalso,
    BusFalso,
    NotificadorFalso,
    PaymentsFalso,
    PlanificadorFalso,
    RatingFalso,
    RepositorioPropuestasFalso,
    UnderFalso,
    oferta_de_rating,
)
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.servicio_emision import ServicioEmision
from app.application.servicio_firma import ServicioFirma
from app.application.servicio_polizas import ServicioPolizas
from app.application.servicio_propuestas import ServicioPropuestas
from app.main import create_app


def _cliente(
    rating: RatingFalso | None = None,
    acl: AclFalso | None = None,
    under: UnderFalso | None = None,
    payments: PaymentsFalso | None = None,
) -> TestClient:
    repositorio = RepositorioPropuestasFalso()
    planificador = PlanificadorFalso()  # la emisión se pide a mano con POST /_emitir
    servicio = ServicioPropuestas(repositorio, rating or RatingFalso())
    firma = ServicioFirma(repositorio, acl or AclFalso(), planificador)
    emision = ServicioEmision(
        repositorio,
        under or UnderFalso(),
        payments or PaymentsFalso(),
        BusFalso(),
        planificador,
        NotificadorFalso(),
        "tok_recaudo",
    )
    config = Configuracion("", "http://rating", 1.0, "ninguna")
    return TestClient(
        create_app(
            config=config,
            servicio_propuestas=servicio,
            servicio_firma=firma,
            servicio_emision=emision,
            servicio_polizas=ServicioPolizas(repositorio),
        )
    )


def test_health() -> None:
    with _cliente() as client:
        assert client.get("/health").json() == {"servicio": "policy", "estado": "ok"}


def test_post_propuestas_acepta_la_oferta_y_get_la_devuelve() -> None:
    with _cliente() as client:
        creada = client.post(
            "/propuestas", json={"oferta_id": "oferta-1", "actor_usuario_id": "u1"}
        )
        leida = client.get(f"/propuestas/{creada.json()['id']}")

    assert creada.status_code == 200
    assert creada.json()["estado"] == "ACEPTADA"
    assert creada.json()["prima"] == {"valor": "185000", "moneda": "COP"}
    assert leida.status_code == 200
    assert leida.json()["id"] == creada.json()["id"]


def test_post_propuestas_dos_veces_devuelve_la_misma() -> None:
    with _cliente() as client:
        primera = client.post("/propuestas", json={"oferta_id": "oferta-1"}).json()
        segunda = client.post("/propuestas", json={"oferta_id": "oferta-1"}).json()

    assert primera["id"] == segunda["id"]


def test_oferta_preliminar_responde_422_con_el_motivo() -> None:
    oferta = oferta_de_rating(tipo="PRELIMINAR", prima=None)
    with _cliente(RatingFalso({"oferta-1": oferta})) as client:
        r = client.post("/propuestas", json={"oferta_id": "oferta-1"})

    assert r.status_code == 422
    assert r.json()["detail"] == "OFERTA_PRELIMINAR"


def test_oferta_desconocida_responde_404() -> None:
    with _cliente() as client:
        r = client.post("/propuestas", json={"oferta_id": "no-existe"})

    assert r.status_code == 404
    assert r.json()["detail"] == "OFERTA_NO_ENCONTRADA"


def test_rating_caido_responde_503_para_que_el_bff_ofrezca_reintentar() -> None:
    with _cliente(RatingFalso(cae=True)) as client:
        r = client.post("/propuestas", json={"oferta_id": "oferta-1"})

    assert r.status_code == 503
    assert r.json()["detail"] == "RATING_NO_DISPONIBLE"


def test_propuesta_desconocida_responde_404() -> None:
    with _cliente() as client:
        assert client.get("/propuestas/no-existe").status_code == 404


FIRMA = {"autoriza_cobro": True, "version_documento": "v1", "firmante_id": "usuario-1"}


def _propuesta(client: TestClient) -> str:
    return client.post("/propuestas", json={"oferta_id": "oferta-1"}).json()["id"]


def test_documento_de_firma_con_el_monto_de_la_clausula_de_cobro() -> None:
    with _cliente() as client:
        r = client.get(f"/propuestas/{_propuesta(client)}/documento-firma")

    assert r.status_code == 200
    assert r.json()["monto_mensual"] == {"valor": "185000", "moneda": "COP"}
    assert r.json()["meses"] == 60
    assert r.json()["version"] == "v1"


def test_firmar_responde_la_evidencia_y_la_propuesta_queda_en_emision() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client)
        r = client.post(f"/propuestas/{propuesta_id}/firma", json=FIRMA)
        leida = client.get(f"/propuestas/{propuesta_id}")

    assert r.status_code == 200
    assert r.json()["estado"] == "EN_EMISION"
    assert r.json()["referencia"] == f"fe-{propuesta_id}"
    assert leida.json()["estado"] == "EN_EMISION"


def test_firmar_dos_veces_devuelve_la_misma_referencia() -> None:
    with _cliente() as client:
        propuesta_id = _propuesta(client)
        primera = client.post(f"/propuestas/{propuesta_id}/firma", json=FIRMA).json()
        segunda = client.post(f"/propuestas/{propuesta_id}/firma", json=FIRMA).json()

    assert primera["referencia"] == segunda["referencia"]


def test_sin_autorizar_el_cobro_responde_422() -> None:
    with _cliente() as client:
        r = client.post(
            f"/propuestas/{_propuesta(client)}/firma", json={**FIRMA, "autoriza_cobro": False}
        )

    assert r.status_code == 422
    assert r.json()["detail"] == "COBRO_NO_AUTORIZADO"


def test_documento_desactualizado_responde_409() -> None:
    with _cliente() as client:
        r = client.post(
            f"/propuestas/{_propuesta(client)}/firma", json={**FIRMA, "version_documento": "v0"}
        )

    assert r.status_code == 409
    assert r.json()["detail"] == "VERSION_DOCUMENTO_DESACTUALIZADA"


def test_proveedor_de_firma_caido_responde_503_para_reintentar() -> None:
    from app.application.cliente_acl import ResultadoFirma

    acl = AclFalso([ResultadoFirma(estado="pendiente", motivo="acl_no_disponible")])
    with _cliente(acl=acl) as client:
        r = client.post(f"/propuestas/{_propuesta(client)}/firma", json=FIRMA)

    assert r.status_code == 503
    assert r.json()["detail"] == "SERVICIO_FIRMA_NO_DISPONIBLE"


def test_firmar_una_propuesta_desconocida_responde_404() -> None:
    with _cliente() as client:
        assert client.post("/propuestas/no-existe/firma", json=FIRMA).status_code == 404
        assert client.get("/propuestas/no-existe/documento-firma").status_code == 404


def _firmada(client: TestClient) -> str:
    propuesta_id = _propuesta(client)
    client.post(f"/propuestas/{propuesta_id}/firma", json=FIRMA)
    return propuesta_id


def test_emitir_deja_la_propuesta_emitida_con_su_poliza() -> None:
    with _cliente() as client:
        propuesta_id = _firmada(client)
        r = client.post(f"/propuestas/{propuesta_id}/_emitir")
        propuesta = client.get(f"/propuestas/{propuesta_id}").json()
        poliza = client.get(f"/polizas/{propuesta['poliza_id']}")

    assert r.status_code == 200
    assert r.json() == {"estado": "EMITIDA"}
    assert propuesta["estado"] == "EMITIDA"
    assert poliza.status_code == 200
    assert poliza.json()["numero"].startswith("SOL-")
    assert poliza.json()["prima"] == {"valor": "185000", "moneda": "COP"}
    assert poliza.json()["actor_usuario_id"] is None  # la propuesta se aceptó sin sesión


def test_emitir_dos_veces_devuelve_la_misma_poliza() -> None:
    with _cliente() as client:
        propuesta_id = _firmada(client)
        client.post(f"/propuestas/{propuesta_id}/_emitir")
        primera = client.get(f"/propuestas/{propuesta_id}").json()["poliza_id"]
        r = client.post(f"/propuestas/{propuesta_id}/_emitir")
        segunda = client.get(f"/propuestas/{propuesta_id}").json()["poliza_id"]

    assert r.json() == {"estado": "EMITIDA"}
    assert primera == segunda


def test_emitir_con_el_cobro_pendiente_deja_la_propuesta_esperando() -> None:
    with _cliente(payments=PaymentsFalso(["pendiente"])) as client:
        propuesta_id = _firmada(client)
        r = client.post(f"/propuestas/{propuesta_id}/_emitir")
        propuesta = client.get(f"/propuestas/{propuesta_id}").json()

    assert r.json() == {"estado": "PENDIENTE_PAGO"}
    assert propuesta["poliza_id"] is None


def test_emitir_cuando_under_pide_revision_expone_los_motivos() -> None:
    under = UnderFalso("REVISION_MANUAL", ["RIESGO_ALTO"])
    with _cliente(under=under) as client:
        propuesta_id = _firmada(client)
        client.post(f"/propuestas/{propuesta_id}/_emitir")
        propuesta = client.get(f"/propuestas/{propuesta_id}").json()

    assert propuesta["estado"] == "EN_REVISION"
    assert propuesta["motivo_estado"] == "RIESGO_ALTO"


def test_emitir_con_under_caido_responde_503_para_que_cloud_tasks_reintente() -> None:
    with _cliente(under=UnderFalso(cae=True)) as client:
        propuesta_id = _firmada(client)
        r = client.post(f"/propuestas/{propuesta_id}/_emitir")

    assert r.status_code == 503
    assert r.json()["detail"] == "EMISION_PENDIENTE"


def test_emitir_una_propuesta_desconocida_responde_404() -> None:
    with _cliente() as client:
        assert client.post("/propuestas/no-existe/_emitir").status_code == 404


def test_poliza_desconocida_responde_404() -> None:
    with _cliente() as client:
        assert client.get("/polizas/no-existe").status_code == 404


def test_el_documento_de_la_poliza_es_un_pdf() -> None:
    with _cliente() as client:
        propuesta_id = _firmada(client)
        client.post(f"/propuestas/{propuesta_id}/_emitir")
        poliza_id = client.get(f"/propuestas/{propuesta_id}").json()["poliza_id"]
        r = client.post(f"/polizas/{poliza_id}/documento", json={"asegurado": "Ana Maria Perez"})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert r.content.startswith(b"%PDF-")
    assert b"Ana Maria Perez" in r.content
    assert b"SOL-" in r.content


def test_el_documento_se_genera_sin_el_nombre_del_asegurado() -> None:
    with _cliente() as client:
        propuesta_id = _firmada(client)
        client.post(f"/propuestas/{propuesta_id}/_emitir")
        poliza_id = client.get(f"/propuestas/{propuesta_id}").json()["poliza_id"]
        r = client.post(f"/polizas/{poliza_id}/documento", json={})

    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


def test_el_documento_de_una_poliza_desconocida_responde_404() -> None:
    with _cliente() as client:
        assert client.post("/polizas/no-existe/documento", json={}).status_code == 404


def test_la_poliza_dice_quien_firmo_y_si_ya_se_aviso() -> None:
    with _cliente() as client:
        propuesta_id = _firmada(client)
        client.post(f"/propuestas/{propuesta_id}/_emitir")
        poliza_id = client.get(f"/propuestas/{propuesta_id}").json()["poliza_id"]
        poliza = client.get(f"/polizas/{poliza_id}").json()

    assert poliza["firmante_id"] == "usuario-1"
    assert poliza["notificada_en"] is not None


def test_el_documento_solo_se_pide_por_post_para_que_el_nombre_no_viaje_en_la_url() -> None:
    rutas = {
        ruta.path: ruta.methods
        for ruta in create_app(Configuracion("", "", 1.0, "ninguna")).routes
        if hasattr(ruta, "methods")
    }

    assert rutas["/polizas/{poliza_id}/documento"] == {"POST"}
