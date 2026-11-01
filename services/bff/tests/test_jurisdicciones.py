"""T18: el BFF lee el plazo de `jurisdicciones.json` y lo expone en `jurisdicciones`."""

import json
from pathlib import Path

import pytest

from app import jurisdicciones as j
from app.idioma import traducir
from app.servicio_oferta import MENSAJE_PLAZO_INVALIDO

CONTRATO = Path(__file__).resolve().parents[3] / "contracts" / "jurisdicciones.json"


def test_los_limites_son_6_y_200_para_los_siete_paises() -> None:
    assert (j.PLAZO_MINIMO_MESES, j.PLAZO_MAXIMO_MESES) == (6, 200)
    assert sorted(x.codigo for x in j.JURISDICCIONES) == ["BR", "CL", "CO", "ES", "MX", "PE", "US"]


def test_el_mensaje_lleva_los_limites_y_se_traduce() -> None:
    assert "entre 6 y 200 meses" in MENSAJE_PLAZO_INVALIDO
    esperado = "The loan term must be between 6 and 200 months."
    assert traducir(MENSAJE_PLAZO_INVALIDO, "en") == esperado


def test_cambiar_el_json_cambia_los_limites(tmp_path) -> None:
    datos = json.loads(j.RUTA.read_text(encoding="utf-8"))
    datos[0]["plazo_max"] = 120
    ruta = tmp_path / "jurisdicciones.json"
    ruta.write_text(json.dumps(datos))

    assert max(x.plazo_max for x in j.cargar(ruta)) == 200  # los demás países siguen en 200
    assert j.cargar(ruta)[0].plazo_max == 120


def test_sin_el_archivo_falla_con_un_error_claro(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="jurisdicciones.json"):
        j.cargar(tmp_path / "no-existe.json")


@pytest.mark.skipif(not CONTRATO.is_file(), reason="sin el árbol del repo (dentro de la imagen)")
def test_la_copia_es_igual_al_contrato() -> None:
    assert j.RUTA.read_bytes() == CONTRATO.read_bytes(), (
        "Corre `python scripts/sincronizar_jurisdicciones.py`"
    )
