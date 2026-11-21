"""HU-M04 + HU-W08 (KAN-259, KAN-290): toda póliza de auto lleva una cobertura de clima en la
celda de su ciudad de circulación, y Cloud Scheduler consulta cada celda vigente."""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime

from fakes import (
    AclFalso,
    BusFalso,
    NotificadorFalso,
    PaymentsFalso,
    PlanificadorFalso,
    RatingFalso,
    RepositorioCoberturasParametricasFalso,
    RepositorioPropuestasFalso,
    UnderFalso,
)
from fastapi.testclient import TestClient

from app.application.clima_auto import PARAMETROS, consultar_celdas, coordenadas, incluir_clima
from app.application.servicio_emision import ServicioEmision
from app.application.servicio_firma import ServicioFirma
from app.application.servicio_propuestas import ServicioPropuestas
from app.main import create_app

DESDE = datetime(2026, 11, 1, tzinfo=UTC)
HASTA = datetime(2027, 11, 1, tzinfo=UTC)
POLIZA = {"id": "poliza-1", "vigencia_desde": DESDE, "vigencia_hasta": HASTA}


def _auto(ciudad: str | None = "Bogotá") -> dict:
    return {"producto": "AUTO", "vehiculo": {"marca": "MAZDA", "ciudadCirculacion": ciudad}}


def test_las_coordenadas_no_dependen_de_tildes_ni_mayusculas():
    assert coordenadas("Bogotá") == coordenadas("BOGOTA") == coordenadas("bogota") == (4.61, -74.08)
    assert coordenadas("Medellín") == (6.25, -75.56)
    assert coordenadas("Ciudad Gótica") is None
    assert coordenadas(None) is None


def test_la_poliza_de_auto_recibe_la_cobertura_de_clima_de_su_ciudad():
    coberturas = RepositorioCoberturasParametricasFalso()
    creada = incluir_clima(coberturas, _auto(), POLIZA)
    assert creada is not None
    assert creada["tipo"] == "CLIMA" and creada["poliza_id"] == "poliza-1"
    assert (creada["latitud"], creada["longitud"]) == (4.61, -74.08)
    assert creada["variable"] == PARAMETROS["variable"]
    assert creada["umbral"] == PARAMETROS["umbral"]
    assert creada["monto_fijo"] == {"valor": PARAMETROS["monto"], "moneda": "COP"}
    assert (creada["vigencia_desde"], creada["vigencia_hasta"]) == (DESDE, HASTA)


def test_es_idempotente():
    coberturas = RepositorioCoberturasParametricasFalso()
    incluir_clima(coberturas, _auto(), POLIZA)
    assert incluir_clima(coberturas, _auto(), POLIZA) is None
    assert len(coberturas.de_poliza("poliza-1")) == 1


def test_una_poliza_de_vida_no_lleva_clima():
    coberturas = RepositorioCoberturasParametricasFalso()
    assert incluir_clima(coberturas, {"producto": "VIDA_DEUDOR"}, POLIZA) is None
    assert incluir_clima(coberturas, {}, POLIZA) is None
    assert coberturas.de_poliza("poliza-1") == []


def test_una_ciudad_fuera_del_catalogo_se_queda_sin_clima_y_no_se_loguea(caplog):
    coberturas = RepositorioCoberturasParametricasFalso()
    with caplog.at_level(logging.INFO):
        assert incluir_clima(coberturas, _auto("Ciudad Gótica"), POLIZA) is None
    assert coberturas.de_poliza("poliza-1") == []
    assert "Gótica" not in caplog.text


def _emision(coberturas):
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    ServicioFirma(repositorio, AclFalso()).firmar(propuesta["id"], True, "v1", "usuario-1")
    repositorio._propuesta(propuesta["id"]).update(_auto("Cali"))
    servicio = ServicioEmision(
        repositorio, UnderFalso(), PaymentsFalso(), BusFalso(), PlanificadorFalso(),
        NotificadorFalso(), "tok_recaudo", 10.0, 12, coberturas_parametricas=coberturas,
    )  # fmt: skip
    return servicio, repositorio, propuesta["id"]


def test_emitir_una_poliza_de_auto_le_agrega_el_clima():
    coberturas = RepositorioCoberturasParametricasFalso()
    servicio, repositorio, propuesta_id = _emision(coberturas)
    assert servicio.emitir(propuesta_id) == "EMITIDA"
    poliza_id = repositorio.obtener(propuesta_id)["poliza_id"]
    [clima] = coberturas.de_poliza(poliza_id)
    assert (clima["latitud"], clima["longitud"]) == (3.45, -76.53)
    servicio.emitir(propuesta_id)
    assert len(coberturas.de_poliza(poliza_id)) == 1


def test_emitir_pagada_tambien_agrega_el_clima():
    coberturas = RepositorioCoberturasParametricasFalso()
    servicio, repositorio, propuesta_id = _emision(coberturas)
    repositorio._propuesta(propuesta_id)["estado"] = "FIRMADA"  # el pago ya se confirmó (W03-29)
    poliza = servicio.emitir_pagada(propuesta_id)
    assert poliza is not None
    assert len(coberturas.de_poliza(poliza["id"])) == 1


class _CoberturasRotas(RepositorioCoberturasParametricasFalso):
    def crear_clima(self, *args, **kwargs):
        raise RuntimeError("base caída")


def test_si_el_clima_falla_la_poliza_queda_emitida():
    servicio, repositorio, propuesta_id = _emision(_CoberturasRotas())
    assert servicio.emitir(propuesta_id) == "EMITIDA"
    assert repositorio.obtener(propuesta_id)["estado"] == "EMITIDA"


class _AclClima:
    def __init__(self, publica: bool = True) -> None:
        self.consultas: list[tuple[float, float, str]] = []
        self.publica = publica

    def consultar_clima(self, latitud: float, longitud: float, variable: str) -> bool:
        self.consultas.append((latitud, longitud, variable))
        return self.publica


def test_se_consulta_una_vez_cada_celda_aunque_tenga_varias_polizas():
    coberturas = RepositorioCoberturasParametricasFalso()
    incluir_clima(coberturas, _auto("Bogotá"), POLIZA)
    incluir_clima(coberturas, _auto("bogota"), {**POLIZA, "id": "poliza-2"})
    incluir_clima(coberturas, _auto("Medellín"), {**POLIZA, "id": "poliza-3"})
    acl = _AclClima()
    assert consultar_celdas(coberturas, acl) == {"celdas": 2, "publicadas": 2}
    assert sorted(acl.consultas) == [
        (4.61, -74.08, "PRECIPITACION_MM"),
        (6.25, -75.56, "PRECIPITACION_MM"),
    ]


@contextmanager
def _cliente(coberturas, acl):
    app = create_app(
        servicio_propuestas=object(),
        servicio_firma=object(),
        servicio_emision=object(),
        servicio_polizas=object(),
        servicio_firma_y_pago=object(),
        servicio_purga=object(),
        repositorio_coberturas=coberturas,
        consulta_clima=acl,
    )
    with TestClient(app) as cliente:
        yield cliente


def test_el_endpoint_del_scheduler_responde_200_aunque_el_acl_no_publique():
    coberturas = RepositorioCoberturasParametricasFalso()
    incluir_clima(coberturas, _auto(), POLIZA)
    with _cliente(coberturas, _AclClima(publica=False)) as cliente:
        respuesta = cliente.post("/coberturas-parametricas/_consultar-clima")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"celdas": 1, "publicadas": 0}
