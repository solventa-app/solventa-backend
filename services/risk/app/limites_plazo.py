"""Plazo mínimo y máximo de la política de crédito, de `jurisdicciones.json` (copia de
`contracts/jurisdicciones.json`; T18). Sin valores por defecto: si el archivo falta, falla con
un error claro. La política base es la de Colombia (sus montos son en COP)."""

import json
from pathlib import Path

RUTA = Path(__file__).resolve().parent / "jurisdicciones.json"
PAIS_BASE = "CO"


def limites_plazo(ruta: Path = RUTA, pais: str = PAIS_BASE) -> tuple[int, int]:
    try:
        for j in json.loads(ruta.read_text(encoding="utf-8")):
            if j["codigo"] == pais:
                return int(j["plazo_min"]), int(j["plazo_max"])
        raise KeyError(pais)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"No se pudo leer el plazo de {pais} en {ruta.name}: la imagen debe llevar la copia "
            "de contracts/jurisdicciones.json (scripts/sincronizar_jurisdicciones.py)."
        ) from error
