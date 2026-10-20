"""La imagen lleva su propia copia de `contracts/fuentes.json` (Bloque 5): debe seguir igual."""

import json
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
CONTRATO = Path(__file__).resolve().parents[3] / "contracts" / "fuentes.json"


def test_la_copia_de_la_imagen_existe_y_es_json_valido() -> None:
    copia = APP / "fuentes.json"

    assert copia.is_file()
    assert {f["nombre"] for f in json.loads(copia.read_text(encoding="utf-8"))}


@pytest.mark.skipif(
    not CONTRATO.is_file(), reason="sin el árbol del repo (p. ej. dentro de la imagen)"
)
def test_la_copia_es_igual_al_contrato() -> None:
    assert (APP / "fuentes.json").read_bytes() == CONTRATO.read_bytes(), (
        "Corre `python scripts/sincronizar_fuentes.py`"
    )
