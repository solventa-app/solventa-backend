"""Catálogo de fuentes de datos por configuración (`contracts/fuentes.json`,
Bloque 5 · EC033).

Permite registrar o enrutar fuentes adicionales (p. ej. `calificacion-buro-v2`)
modificando únicamente la configuración, sin tocar el dominio ni los casos de
uso (`ServicioConsultaFuentes`). Si el archivo no está montado dentro del
contenedor, usa las 5 fuentes por defecto del Sprint 1.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OrigenFuente = Literal["OPEN_FINANCE", "OPEN_DATA"]


@dataclass(frozen=True)
class DefinicionFuente:
    nombre: str
    origen: str
    peso: str = "0.00"
    obligatoria: bool = False


FUENTES_POR_DEFECTO: tuple[DefinicionFuente, ...] = (
    DefinicionFuente(
        nombre="cuentas-bancarias",
        origen="OPEN_FINANCE",
        peso="0.20",
        obligatoria=False,
    ),
    DefinicionFuente(
        nombre="historial-crediticio",
        origen="OPEN_FINANCE",
        peso="0.30",
        obligatoria=True,
    ),
    DefinicionFuente(
        nombre="afiliacion-pila",
        origen="OPEN_DATA",
        peso="0.20",
        obligatoria=False,
    ),
    DefinicionFuente(
        nombre="camara-comercio",
        origen="OPEN_DATA",
        peso="0.15",
        obligatoria=False,
    ),
    DefinicionFuente(
        nombre="antecedentes-judiciales",
        origen="OPEN_DATA",
        peso="0.15",
        obligatoria=False,
    ),
)


def _resolver_ruta(ruta: str | Path | None = None) -> Path | None:
    candidata = ruta or os.getenv("FUENTES_CONFIG_PATH")
    if candidata:
        try:
            p = Path(candidata)
            if p.is_file():
                return p
        except OSError:
            pass
    actual = Path(__file__).resolve()
    # La copia que lleva la imagen (`app/fuentes.json`), idéntica al contrato.
    copia = actual.parents[1] / "fuentes.json"
    if copia.is_file():
        return copia
    for ancestro in actual.parents:
        try:
            archivo = ancestro / "contracts" / "fuentes.json"
            if archivo.is_file():
                return archivo
        except OSError:
            continue
    return None


def cargar_fuentes(ruta: str | Path | None = None) -> tuple[DefinicionFuente, ...]:
    archivo = _resolver_ruta(ruta)
    if archivo is None:
        return FUENTES_POR_DEFECTO
    try:
        crudo = json.loads(archivo.read_text(encoding="utf-8"))
    except Exception:
        return FUENTES_POR_DEFECTO

    elementos = crudo.get("fuentes", []) if isinstance(crudo, dict) else crudo
    if not isinstance(elementos, list) or not elementos:
        return FUENTES_POR_DEFECTO

    return tuple(
        DefinicionFuente(
            nombre=str(item["nombre"]),
            origen=str(item["origen"]),
            peso=str(item.get("peso", "0.00")),
            obligatoria=bool(item.get("obligatoria", False)),
        )
        for item in elementos
    )


def fuentes_por_origen(origen: str, ruta: str | Path | None = None) -> tuple[str, ...]:
    return tuple(f.nombre for f in cargar_fuentes(ruta) if f.origen == origen)


def fuentes_open_finance(ruta: str | Path | None = None) -> tuple[str, ...]:
    return fuentes_por_origen("OPEN_FINANCE", ruta)


def fuentes_open_data(ruta: str | Path | None = None) -> tuple[str, ...]:
    return fuentes_por_origen("OPEN_DATA", ruta)


def todas_las_fuentes(ruta: str | Path | None = None) -> tuple[str, ...]:
    return tuple(f.nombre for f in cargar_fuentes(ruta))
