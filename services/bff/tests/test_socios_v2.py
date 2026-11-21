"""API de socios `/v2` (v0.63, HA-MOD-003, EC033): convive con `/v1` y solo cambia la forma de la
oferta de `POST /v2/cotizaciones` (un `precio` en lugar de `prima` y `rangoPrima`)."""

import json
from pathlib import Path

import pytest
from fakes import OFERTA_PRELIMINAR
from jsonschema import Draft202012Validator
from jsonschema.validators import RefResolver
from test_socios_api import COTIZACION, Entorno, _es_error_publico

from app.socios.api import api_de, cotizacion_v2, es_ruta_de_socios

CONTRATO_V2 = Path(__file__).resolve().parent.parent / "app" / "socios-v2.json"


@pytest.fixture
def e() -> Entorno:
    return Entorno()


def _valida_contra_el_contrato(cuerpo: dict) -> None:
    contrato = json.loads(CONTRATO_V2.read_text(encoding="utf-8"))
    esquema = contrato["components"]["schemas"]["CotizacionV2"]
    Draft202012Validator(esquema, resolver=RefResolver("", contrato)).validate(cuerpo)


def test_completa_trae_un_precio_exacto(e) -> None:
    with e.cliente() as client:
        respuesta = client.post("/v2/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    oferta = cuerpo["oferta"]
    assert "prima" not in oferta and "rangoPrima" not in oferta
    assert oferta["tipo"] == "COMPLETA"
    assert oferta["precio"]["tipo"] == "EXACTO"
    assert set(oferta["precio"]) == {"tipo", "valor", "moneda"}
    assert cuerpo["presupuestoRestante"] == 1
    _valida_contra_el_contrato(cuerpo)


def test_preliminar_trae_un_rango(e) -> None:
    e.rating.oferta = OFERTA_PRELIMINAR
    with e.cliente() as client:
        respuesta = client.post("/v2/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    assert respuesta.status_code == 200, respuesta.text
    precio = respuesta.json()["oferta"]["precio"]
    rango = OFERTA_PRELIMINAR["rangoPrima"]
    assert precio == {
        "tipo": "RANGO",
        "minima": rango["minima"]["valor"],
        "maxima": rango["maxima"]["valor"],
        "moneda": rango["minima"]["moneda"],
    }
    _valida_contra_el_contrato(respuesta.json())


def test_v1_sigue_igual(e) -> None:
    with e.cliente() as client:
        v1 = client.post("/v1/cotizaciones", json=COTIZACION, headers=e.cabeceras()).json()
        v2 = client.post("/v2/cotizaciones", json=COTIZACION, headers=e.cabeceras()).json()

    assert "prima" in v1["oferta"] and "precio" not in v1["oferta"]
    # Fuera del precio, la oferta es la misma.
    sin_precio = {k: v for k, v in v2["oferta"].items() if k not in ("precio", "id")}
    assert sin_precio == {
        k: v for k, v in v1["oferta"].items() if k not in ("prima", "rangoPrima", "id")
    }


def test_descuenta_presupuesto_y_cuenta_como_cotizaciones(e) -> None:
    with e.cliente() as client:
        client.post("/v2/cotizaciones", json=COTIZACION, headers=e.cabeceras())

    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 1
    assert ("socio-1", "PRODUCCION", "cotizaciones", "OK") in e.socios.consumos


def test_los_errores_son_los_de_v1(e) -> None:
    with e.cliente() as client:
        invalida = client.post(
            "/v2/cotizaciones", json={**COTIZACION, "plazoMeses": 201}, headers=e.cabeceras()
        )
        sin_token = client.post("/v2/cotizaciones", json=COTIZACION)

    _es_error_publico(invalida, 422, "DATOS_INVALIDOS")
    _es_error_publico(sin_token, 401, "TOKEN_INVALIDO")
    assert e.socios.consumidas == {}


def test_la_llave_de_v2_no_se_cruza_con_la_de_v1(e) -> None:
    llave = {**e.cabeceras(), "Idempotency-Key": "misma"}
    with e.cliente() as client:
        v1 = client.post("/v1/cotizaciones", json=COTIZACION, headers=llave)
        v2 = client.post("/v2/cotizaciones", json=COTIZACION, headers=llave)
        repetida = client.post("/v2/cotizaciones", json=COTIZACION, headers=llave)

    assert "prima" in v1.json()["oferta"] and "precio" in v2.json()["oferta"]
    assert repetida.headers["idempotent-replayed"] == "true"
    assert repetida.json() == v2.json()
    assert e.socios.consumidas[("socio-1", "PRODUCCION")] == 2


def test_el_contrato_v2_es_publico(e) -> None:
    with e.cliente() as client:
        respuesta = client.get("/v2/openapi.json")

    assert respuesta.status_code == 200
    assert set(respuesta.json()["paths"]) == {"/v2/openapi.json", "/v2/cotizaciones"}


def test_las_rutas_v2_son_de_socios_y_cuentan_en_su_api() -> None:
    assert es_ruta_de_socios("/v2/cotizaciones")
    assert not es_ruta_de_socios("/v2x/cotizaciones")
    assert api_de("/v2/cotizaciones") == "cotizaciones"
    assert api_de("/v1/cotizaciones") == "cotizaciones"


def test_el_adaptador_conserva_el_orden_y_el_presupuesto() -> None:
    cotizacion = {
        "oferta": {
            "id": "o",
            "tipo": "COMPLETA",
            "prima": {"valor": "1.00", "moneda": "COP"},
            "rangoPrima": None,
            "cobertura": {"valor": "2", "moneda": "COP"},
        },
        "presupuestoRestante": 7,
    }
    nueva = cotizacion_v2(cotizacion)
    assert list(nueva["oferta"]) == ["id", "tipo", "precio", "cobertura"]
    assert nueva["presupuestoRestante"] == 7
