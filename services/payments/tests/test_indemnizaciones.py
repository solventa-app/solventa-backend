from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.indemnizaciones import (
    OrdenEnConflicto,
    OrdenInvalida,
    ServicioIndemnizaciones,
)
from app.main import create_app


class RepositorioIndemnizacionesFalso:
    def __init__(self):
        self.por_siniestro = {}

    def crear_o_obtener(self, datos):
        if datos["siniestro_id"] not in self.por_siniestro:
            self.por_siniestro[datos["siniestro_id"]] = {
                "id": f"ind-{len(self.por_siniestro) + 1}",
                **datos,
                "estado": "PENDIENTE_DISPERSION",
                "creado_en": datetime(2026, 10, 28, tzinfo=UTC),
                "actualizado_en": datetime(2026, 10, 28, tzinfo=UTC),
            }
        return dict(self.por_siniestro[datos["siniestro_id"]])

    def obtener(self, indemnizacion_id):
        return next((o for o in self.por_siniestro.values() if o["id"] == indemnizacion_id), None)


ORDEN = {"siniestro_id": "sin-01", "poliza_id": "pol-01", "monto": "8650000", "moneda": "COP"}


def test_la_orden_queda_pendiente_de_dispersion_y_es_idempotente():
    servicio = ServicioIndemnizaciones(RepositorioIndemnizacionesFalso())
    primera = servicio.ordenar(ORDEN)
    assert primera["estado"] == "PENDIENTE_DISPERSION"
    assert primera["metodo_transferencia"] == "ACH Colombia Interbancario"
    assert servicio.ordenar(ORDEN)["id"] == primera["id"]


def test_otro_monto_para_el_mismo_siniestro_es_conflicto():
    servicio = ServicioIndemnizaciones(RepositorioIndemnizacionesFalso())
    servicio.ordenar(ORDEN)
    with pytest.raises(OrdenEnConflicto):
        servicio.ordenar({**ORDEN, "monto": "1"})


@pytest.mark.parametrize(
    "cambio", [{"monto": "-5"}, {"monto": "1e5"}, {"monto": "0"}, {"moneda": "co"}]
)
def test_rechaza_montos_y_monedas_invalidos(cambio):
    with pytest.raises(OrdenInvalida):
        ServicioIndemnizaciones(RepositorioIndemnizacionesFalso()).ordenar({**ORDEN, **cambio})


def test_rutas_de_indemnizaciones():
    servicio = ServicioIndemnizaciones(RepositorioIndemnizacionesFalso())
    app = create_app(
        Configuracion.desde_entorno(),
        servicio_cobros=object(),
        servicio_indemnizaciones=servicio,
    )
    with TestClient(app) as c:
        r = c.post("/indemnizaciones", json=ORDEN)
        assert r.status_code == 200 and r.json()["estado"] == "PENDIENTE_DISPERSION"
        assert c.post("/indemnizaciones", json=ORDEN).json()["id"] == r.json()["id"]
        assert c.post("/indemnizaciones", json={**ORDEN, "monto": "9"}).status_code == 409
        assert c.post("/indemnizaciones", json={**ORDEN, "monto": "abc"}).status_code == 422
        assert c.get(f"/indemnizaciones/{r.json()['id']}").json()["monto"] == "8650000"
        assert c.get("/indemnizaciones/no-existe").status_code == 404
