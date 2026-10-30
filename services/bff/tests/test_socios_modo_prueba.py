"""Modo prueba de la API de socios (HU-W07, W07-10): con un token de PRUEBAS el flujo completo
responde con datos simulados y nunca llama a las fuentes, a RATING ni a POLICY."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_socios_api import CONTACTO, Entorno

from app.main import create_app
from app.socios.modo_prueba import CODIGO_DE_PRUEBA, SEGUNDOS_EMISION


class Prueba:
    def __init__(self) -> None:
        self.e = Entorno()
        self.cabeceras = self.e.cabeceras(entorno="PRUEBAS")
        self.cliente = TestClient(create_app(config=self.e.config, clientes=self.e.clientes))

    def __enter__(self) -> "Prueba":
        self.cliente.__enter__()
        modo = self.cliente.app.state.modo_prueba
        self.ahora = modo._reloj()
        modo._reloj = lambda: self.ahora
        return self

    def __exit__(self, *args) -> None:
        self.cliente.__exit__(*args)

    def post(self, ruta: str, cuerpo: dict | None = None, **kwargs):
        return self.cliente.post(ruta, json=cuerpo or {}, headers=self.cabeceras, **kwargs)

    def get(self, ruta: str, **kwargs):
        return self.cliente.get(ruta, headers=self.cabeceras, **kwargs)

    def hasta_oferta(self, documento: str = "1000000001") -> tuple[dict, dict]:
        consentimiento = self.post(
            "/v1/consentimientos",
            {"documentoIdentidad": documento, "finalidad": "cotizacion", "versionTexto": "v1"},
        ).json()
        cotizacion = self.post(
            "/v1/cotizaciones",
            {
                "consentimientoId": consentimiento["id"],
                "documentoIdentidad": documento,
                "valorCredito": "150000000",
                "plazoMeses": 60,
                "direccionInmueble": "Calle 1",
            },
        )
        return consentimiento, cotizacion


@pytest.fixture
def p():
    with Prueba() as prueba:
        yield prueba


def test_el_flujo_completo_llega_a_emitida_sin_tocar_los_micros(p) -> None:
    _, cotizacion = p.hasta_oferta()
    oferta = cotizacion.json()["oferta"]
    propuesta = p.post("/v1/propuestas", {"ofertaId": oferta["id"], "contacto": CONTACTO})
    pid = propuesta.json()["id"]
    otra_vez = p.post("/v1/propuestas", {"ofertaId": oferta["id"], "contacto": CONTACTO})
    p.post(f"/v1/propuestas/{pid}/codigo")
    mal = p.post(f"/v1/propuestas/{pid}/codigo/verificar", {"codigo": "123456"})
    p.post(f"/v1/propuestas/{pid}/codigo/verificar", {"codigo": CODIGO_DE_PRUEBA})
    emitida = p.post(f"/v1/propuestas/{pid}/emitir").json()
    en_firma = p.get(f"/v1/propuestas/{pid}").json()
    p.ahora += timedelta(seconds=SEGUNDOS_EMISION + 1)
    final = p.get(f"/v1/propuestas/{pid}").json()
    poliza = p.get(f"/v1/polizas/{final['polizaId']}")
    documento = p.get(f"/v1/polizas/{final['polizaId']}/documento")

    assert cotizacion.status_code == 200 and oferta["id"].startswith("prueba-")
    assert cotizacion.json()["presupuestoRestante"] == 49  # el de PRUEBAS, en AUTH
    assert propuesta.status_code == 201 and otra_vez.json()["id"] == pid
    assert mal.status_code == 409 and mal.json()["intentosRestantes"] == 4
    assert emitida["propuesta"]["estado"] == "EN_FIRMA" and emitida["enlace"]["venceEn"]
    assert en_firma["enlace"]["vigente"] is True
    assert final["estado"] == "EMITIDA" and final["estadoVisible"] == "EMITIDA"
    assert poliza.json()["ramo"] == "MODO PRUEBA"
    assert documento.headers["content-type"] == "application/pdf"
    assert b"MODO PRUEBA" in documento.content
    # W07-10: ni las fuentes, ni RATING, ni POLICY.
    assert p.e.rating.llamadas == [] and p.e.policy.pedidos == [] and p.e.policy.llamadas == []


@pytest.mark.parametrize(
    "documento,estado,codigo",
    [("1000000005", 429, "PRESUPUESTO_AGOTADO"), ("1000000006", 409, "CONSENTIMIENTO_NO_VIGENTE")],
)
def test_documentos_de_prueba_que_fallan_al_cotizar(p, documento, estado, codigo) -> None:
    _, cotizacion = p.hasta_oferta(documento)

    assert cotizacion.status_code == estado and cotizacion.json()["codigo"] == codigo


def test_una_oferta_preliminar_no_se_acepta(p) -> None:
    _, cotizacion = p.hasta_oferta("1000000002")
    oferta = cotizacion.json()["oferta"]
    aceptar = p.post("/v1/propuestas", {"ofertaId": oferta["id"], "contacto": CONTACTO})

    assert oferta["tipo"] == "PRELIMINAR" and oferta["rangoPrima"]
    assert aceptar.status_code == 409 and aceptar.json()["codigo"] == "OFERTA_PRELIMINAR"


@pytest.mark.parametrize(
    "documento,estado", [("1000000003", "NO_EMITIDA"), ("1000000004", "EN_REVISION")]
)
def test_la_suscripcion_simulada_rechaza_o_pide_revision(p, documento, estado) -> None:
    _, cotizacion = p.hasta_oferta(documento)
    pid = p.post(
        "/v1/propuestas", {"ofertaId": cotizacion.json()["oferta"]["id"], "contacto": CONTACTO}
    ).json()["id"]
    p.post(f"/v1/propuestas/{pid}/codigo/verificar", {"codigo": CODIGO_DE_PRUEBA})
    emitida = p.post(f"/v1/propuestas/{pid}/emitir").json()

    assert emitida["propuesta"]["estado"] == estado and emitida["enlace"] is None
    assert emitida["propuesta"]["mensajeEstado"]


def test_lo_del_sandbox_de_un_socio_no_lo_ve_otro(p) -> None:
    _, cotizacion = p.hasta_oferta()
    pid = p.post(
        "/v1/propuestas", {"ofertaId": cotizacion.json()["oferta"]["id"], "contacto": CONTACTO}
    ).json()["id"]
    otro = p.cliente.get(f"/v1/propuestas/{pid}", headers=p.e.cabeceras("socio-2", "PRUEBAS"))

    assert otro.status_code == 404


def test_las_ventas_del_sandbox_son_ficticias_pero_coherentes(p) -> None:
    ventas = p.get("/v1/ventas").json()

    assert len(ventas["meses"]) == 3
    assert ventas["totales"]["emitidas"] == sum(m["emitidas"] for m in ventas["meses"])
