"""El catálogo de ciudades (`contracts/ciudades.json`) y los prestadores de CLAIMS dicen lo mismo.

La app móvil ofrece esas ciudades en una lista desplegable al reportar un siniestro y los marcadores
del mapa son los prestadores. Si un prestador quedara en una ciudad fuera del catálogo, la lista
y el mapa se contradirían."""

import json
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
CATALOGO = json.loads((RAIZ / "contracts" / "ciudades.json").read_text(encoding="utf-8"))
PRESTADORES = json.loads(
    (RAIZ / "services/claims/migrations/datos/prestadores.json").read_text(encoding="utf-8")
)


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    ).lower()


def test_los_ids_y_los_nombres_no_se_repiten() -> None:
    ciudades = CATALOGO["ciudades"]
    assert len({c["id"] for c in ciudades}) == len(ciudades)
    assert len({_sin_tildes(c["nombre"]) for c in ciudades}) == len(ciudades)


def test_cada_ciudad_trae_nombre_y_departamento() -> None:
    for ciudad in CATALOGO["ciudades"]:
        assert ciudad["nombre"].strip()
        assert ciudad["departamento"].strip()


def test_las_ciudades_de_los_prestadores_estan_en_el_catalogo() -> None:
    items = (
        PRESTADORES
        if isinstance(PRESTADORES, list)
        else next(v for v in PRESTADORES.values() if isinstance(v, list))
    )
    nombres = {c["nombre"] for c in CATALOGO["ciudades"]}
    faltan = {p["ciudad"] for p in items} - nombres
    assert not faltan, f"prestadores en ciudades fuera del catálogo: {sorted(faltan)}"
