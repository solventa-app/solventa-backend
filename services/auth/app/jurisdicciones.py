"""Países y tipos de documento soportados (T20), leídos de `jurisdicciones.json` (copia de
`contracts/jurisdicciones.json`, verificada por `scripts/sincronizar_jurisdicciones.py
--verificar`). AUTH solo usa el catálogo de documentos: los límites del crédito son del BFF."""

import json
import re
from pathlib import Path

RUTA = Path(__file__).resolve().parent / "jurisdicciones.json"
SEPARADORES = re.compile(r"[\s.\-]")


def _cargar(ruta: Path = RUTA) -> dict[str, dict]:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return {
            j["codigo"]: {
                "tipo_documento": j["tipo_documento"],
                "tipos": {t["codigo"]: t["formato"] for t in j["tipos_documento"]},
            }
            for j in datos
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"No se pudo leer {ruta.name}: la imagen debe llevar la copia de "
            "contracts/jurisdicciones.json (scripts/sincronizar_jurisdicciones.py)."
        ) from error


CATALOGO = _cargar()
PAISES: tuple[str, ...] = tuple(CATALOGO)


def tipo_por_defecto(pais: str) -> str:
    return CATALOGO[pais]["tipo_documento"]


def limpiar_numero(numero: str) -> str:
    """Sin espacios, puntos ni guiones, y en mayúsculas (como `documentoValido` del BFF)."""
    return SEPARADORES.sub("", numero).upper()


def documento_valido(pais: str, tipo: str, numero: str) -> bool:
    formato = CATALOGO.get(pais, {}).get("tipos", {}).get(tipo)
    return formato is not None and re.fullmatch(formato, limpiar_numero(numero)) is not None
