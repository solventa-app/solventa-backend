"""T18: el plazo mínimo y máximo salen de `jurisdicciones.json`, sin valor por defecto."""

import json
from pathlib import Path

import pytest

from app.application.motor_decision import PoliticaCredito, sugerir_plazo
from app.limites_plazo import limites_plazo

APP = Path(__file__).resolve().parents[1] / "app"
CONTRATO = Path(__file__).resolve().parents[3] / "contracts" / "jurisdicciones.json"


def test_la_politica_toma_6_y_200_del_json() -> None:
    politica = PoliticaCredito()

    assert (politica.plazo_min, politica.plazo_max) == (6, 200)


def test_un_json_con_otros_limites_cambia_la_politica(tmp_path) -> None:
    ruta = tmp_path / "jurisdicciones.json"
    ruta.write_text(json.dumps([{"codigo": "CO", "plazo_min": 12, "plazo_max": 120}]))

    assert limites_plazo(ruta) == (12, 120)


def test_sin_el_archivo_falla_con_un_error_claro(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="jurisdicciones.json"):
        limites_plazo(tmp_path / "jurisdicciones.json")


def test_la_contraoferta_sube_de_6_en_6_y_con_200_la_cuota_es_coherente() -> None:
    from decimal import Decimal

    tasa = Decimal("0.01")
    cuota_max = Decimal("1000000")
    # Capacidad a 200 meses: la cuota máxima soporta un monto finito y creciente con el plazo.
    plazo = sugerir_plazo(Decimal("30000000"), cuota_max, tasa, 6, 200)
    assert 6 < plazo <= 200 and (plazo - 6) % 6 == 0
    # Un monto que ni a 200 meses cabe en la cuota llega al tope sin pasarse.
    assert sugerir_plazo(Decimal("900000000"), cuota_max, tasa, 6, 200) == 200


@pytest.mark.skipif(not CONTRATO.is_file(), reason="sin el árbol del repo (dentro de la imagen)")
def test_la_copia_es_igual_al_contrato() -> None:
    assert (APP / "jurisdicciones.json").read_bytes() == CONTRATO.read_bytes(), (
        "Corre `python scripts/sincronizar_jurisdicciones.py`"
    )
