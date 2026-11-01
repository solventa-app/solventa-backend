"""Catálogo y configuración de jurisdicciones (países) soportadas."""

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.domain import documentos as d

RUTA_JURISDICCIONES = Path(__file__).resolve().parents[1] / "jurisdicciones.json"


@dataclass(frozen=True)
class Jurisdiccion:
    codigo: str  # ISO 3166-1 alfa-2
    nombre: str
    moneda: str  # ISO 4217
    tipo_documento: str
    validar_documento: Callable[[str], str]
    generar_documento: Callable[[random.Random], str]  # solo para pruebas/demos
    regimen_open_finance: str  # marco regulatorio de referencia
    region_datos: str  # data residency ("latam", "us", "eu", etc.)
    monto_min: Decimal
    monto_max: Decimal
    paso_monto: Decimal = Decimal("50")
    plazo_min: int = 6  # el valor real sale de jurisdicciones.json (no hay otra fuente)
    plazo_max: int = 200
    dti_maximo: Decimal = Decimal("0.40")


_REGISTRO_DOCUMENTOS: dict[str, tuple[Callable[[str], str], Callable[[random.Random], str]]] = {
    "CO": (d.validar_cc_co, d.generar_cc_co),
    "MX": (d.validar_curp_mx, d.generar_curp_mx),
    "BR": (d.validar_cpf_br, d.generar_cpf_br),
    "CL": (d.validar_rut_cl, d.generar_rut_cl),
    "PE": (d.validar_dni_pe, d.generar_dni_pe),
    "US": (d.validar_ssn_us, d.generar_ssn_us),
    "ES": (d.validar_dni_es, d.generar_dni_es),
}


def _cargar_jurisdicciones(ruta: Path = RUTA_JURISDICCIONES) -> list[Jurisdiccion]:
    """Lee la copia de contracts/jurisdicciones.json que lleva la imagen (T18). No hay valores
    por defecto: si falta o está mal formada, falla con un error claro."""
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        resultado: list[Jurisdiccion] = []
        for item in datos:
            cod = item["codigo"]
            val_fn, gen_fn = _REGISTRO_DOCUMENTOS.get(
                cod, (lambda v: v.strip(), lambda rng: "12345678")
            )
            resultado.append(
                Jurisdiccion(
                    codigo=cod,
                    nombre=item["nombre"],
                    moneda=item["moneda"],
                    tipo_documento=item["tipo_documento"],
                    validar_documento=val_fn,
                    generar_documento=gen_fn,
                    regimen_open_finance=item.get("regimen_open_finance", ""),
                    region_datos=item.get("region_datos", "latam"),
                    monto_min=Decimal(str(item["monto_min"])),
                    monto_max=Decimal(str(item["monto_max"])),
                    paso_monto=Decimal(str(item.get("paso_monto", 50))),
                    plazo_min=int(item["plazo_min"]),
                    plazo_max=int(item["plazo_max"]),
                    dti_maximo=Decimal(str(item.get("dti_maximo", "0.40"))),
                )
            )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            f"No se pudo leer {ruta.name}: la imagen debe llevar la copia de "
            "contracts/jurisdicciones.json (scripts/sincronizar_jurisdicciones.py)."
        ) from error
    return resultado


_LISTA_JURISDICCIONES = _cargar_jurisdicciones()
JURISDICCIONES: dict[str, Jurisdiccion] = {j.codigo: j for j in _LISTA_JURISDICCIONES}


def obtener_jurisdiccion(codigo: str) -> Jurisdiccion:
    """Retorna la jurisdicción asociada al código ISO de país, o eleva ValueError si no existe."""
    try:
        return JURISDICCIONES[codigo.upper()]
    except KeyError:
        soportados = ", ".join(sorted(JURISDICCIONES))
        raise ValueError(f"País '{codigo}' no soportado. Soportados: {soportados}") from None


def listar_jurisdicciones() -> list[Jurisdiccion]:
    """Retorna la lista completa de jurisdicciones configuradas."""
    return list(JURISDICCIONES.values())
