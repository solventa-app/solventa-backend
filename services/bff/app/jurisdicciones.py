"""Límites por país de la solicitud, leídos de `jurisdicciones.json` (copia de
`contracts/jurisdicciones.json`; T18). No hay valores por defecto: si el archivo falta o no trae
los límites, el servicio no arranca con un error claro en vez de validar con un número viejo."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

RUTA = Path(__file__).resolve().parent / "jurisdicciones.json"


@dataclass(frozen=True)
class Jurisdiccion:
    codigo: str
    nombre: str
    moneda: str
    tipo_documento: str
    tipos_documento: tuple[dict, ...]
    plazo_min: int
    plazo_max: int


def cargar(ruta: Path = RUTA) -> list[Jurisdiccion]:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        jurisdicciones = [
            Jurisdiccion(
                codigo=j["codigo"],
                nombre=j["nombre"],
                moneda=j["moneda"],
                tipo_documento=j["tipo_documento"],
                tipos_documento=tuple(j["tipos_documento"]),
                plazo_min=int(j["plazo_min"]),
                plazo_max=int(j["plazo_max"]),
            )
            for j in datos
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"No se pudo leer {ruta.name}: la imagen debe llevar la copia de "
            "contracts/jurisdicciones.json (scripts/sincronizar_jurisdicciones.py)."
        ) from error
    if not jurisdicciones or any(j.plazo_min > j.plazo_max for j in jurisdicciones):
        raise RuntimeError(f"{ruta.name} no trae jurisdicciones con plazo mínimo y máximo válidos.")
    return jurisdicciones


JURISDICCIONES = cargar()
# La solicitud W01-01 no lleva país: el BFF acepta el rango que cubre a todos los países; el
# motor de RISK/RATING aplica después el límite de la jurisdicción.
PLAZO_MINIMO_MESES = min(j.plazo_min for j in JURISDICCIONES)
PLAZO_MAXIMO_MESES = max(j.plazo_max for j in JURISDICCIONES)


def validar_documento(pais: str, tipo: str, numero: str) -> bool:
    """T20: el tipo existe en el país y el número cumple su formato (catálogo de
    jurisdicciones.json). Los separadores (puntos, guiones, espacios) se ignoran."""
    jurisdiccion = next((x for x in JURISDICCIONES if x.codigo == pais.upper()), None)
    if jurisdiccion is None:
        return False
    definicion = next(
        (t for t in jurisdiccion.tipos_documento if t["codigo"] == tipo.upper()), None
    )
    if definicion is None:
        return False
    limpio = re.sub(r"[\s.\-]", "", numero).upper()
    return re.fullmatch(definicion["formato"], limpio) is not None
