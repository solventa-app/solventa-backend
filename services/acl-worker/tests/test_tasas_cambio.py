"""Tasas de cambio de referencia (KAN-209)."""

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from test_main import CONFIG_SIN_PROVEEDORES

from app.adapters.tasas_cambio import AdaptadorTasasReferencia
from app.main import create_app


def test_endpoint_devuelve_la_tabla_en_texto_decimal() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.get("/tasas-cambio")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["base"] == "COP"
    assert cuerpo["cop_por_unidad"]["COP"] == "1"
    assert {"USD", "EUR", "MXN", "BRL", "CLP", "PEN"} <= set(cuerpo["cop_por_unidad"])
    assert all(Decimal(v) > 0 for v in cuerpo["cop_por_unidad"].values())


@pytest.mark.parametrize(
    "tasas",
    [{"COP": "1", "USD": "0"}, {"COP": "1", "USD": "abc"}, {"COP": "2", "USD": "4000"}],
)
def test_rechaza_una_tabla_invalida(tmp_path, tasas: dict) -> None:
    archivo = tmp_path / "tasas.json"
    archivo.write_text(json.dumps({"fecha": "x", "fuente": "y", "cop_por_unidad": tasas}))
    with pytest.raises(ValueError):
        AdaptadorTasasReferencia(archivo)
