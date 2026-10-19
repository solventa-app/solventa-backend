"""Catálogo y configuración de jurisdicciones (países) soportadas."""

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.domain import documentos as d


@dataclass(frozen=True)
class Jurisdiccion:
    codigo: str                     # ISO 3166-1 alfa-2
    nombre: str
    moneda: str                     # ISO 4217
    tipo_documento: str
    validar_documento: Callable[[str], str]
    generar_documento: Callable[[random.Random], str]  # solo para pruebas/demos
    regimen_open_finance: str       # marco regulatorio de referencia
    region_datos: str               # data residency ("latam", "us", "eu", etc.)
    monto_min: Decimal
    monto_max: Decimal
    paso_monto: Decimal = Decimal("50")
    plazo_min: int = 6
    plazo_max: int = 60
    dti_maximo: Decimal = Decimal("0.40")


_REGISTRO_DOCUMENTOS: dict[str, tuple[Callable[[str], str], Callable[[random.Random], str]]] = {
    "CO": (d.validar_cc_co, d.generar_cc_co),
    "MX": (d.validar_curp_mx, d.generar_curp_mx),
    "BR": (d.validar_cpf_br, d.generar_cpf_br),
    "CL": (d.validar_rut_cl, d.generar_rut_cl),
    "PE": (d.validar_dni_pe, d.generar_dni_pe),
    "US": (d.validar_ssn_us, d.generar_ssn_us),
    "ES": (d.validar_dni_es, d.generar_dni_es),
    "DE": (d.validar_steuerid_de, d.generar_steuerid_de),
    "GB": (d.validar_nino_gb, d.generar_nino_gb),
    "CN": (d.validar_rid_cn, d.generar_rid_cn),
}

_J_DEFAULT: list[Jurisdiccion] = [
    Jurisdiccion(
        "CO", "Colombia", "COP", "CC", d.validar_cc_co, d.generar_cc_co,
        "Finanzas abiertas obligatorias (Decreto 1297/2022, Decreto 0368/2026, SFC)", "latam",
        Decimal("500000"), Decimal("80000000"), Decimal("10000"),
    ),
    Jurisdiccion(
        "MX", "México", "MXN", "CURP", d.validar_curp_mx, d.generar_curp_mx,
        "Ley Fintech, art. 76 (APIs abiertas)", "latam",
        Decimal("2000"), Decimal("400000"), Decimal("100"),
    ),
    Jurisdiccion(
        "BR", "Brasil", "BRL", "CPF", d.validar_cpf_br, d.generar_cpf_br,
        "Open Finance Brasil (BCB)", "latam",
        Decimal("500"), Decimal("100000"), Decimal("50"),
    ),
    Jurisdiccion(
        "CL", "Chile", "CLP", "RUT", d.validar_rut_cl, d.generar_rut_cl,
        "Ley Fintec 21.521 (SFA)", "latam",
        Decimal("200000"), Decimal("30000000"), Decimal("10000"),
    ),
    Jurisdiccion(
        "PE", "Perú", "PEN", "DNI", d.validar_dni_pe, d.generar_dni_pe,
        "Marco en desarrollo (SBS)", "latam",
        Decimal("500"), Decimal("80000"), Decimal("50"),
    ),
    Jurisdiccion(
        "US", "Estados Unidos", "USD", "SSN", d.validar_ssn_us, d.generar_ssn_us,
        "CFPB Section 1033", "us",
        Decimal("500"), Decimal("50000"), Decimal("50"),
    ),
    Jurisdiccion(
        "ES", "España", "EUR", "DNI_NIE", d.validar_dni_es, d.generar_dni_es,
        "PSD2 / Berlin Group (UE)", "eu",
        Decimal("500"), Decimal("60000"), Decimal("50"),
    ),
]


def _cargar_jurisdicciones() -> list[Jurisdiccion]:
    # Intentar resolver desde contracts/jurisdicciones.json relativo a la raíz del repo
    candidatos = [
        Path(__file__).resolve().parents[4] / "contracts" / "jurisdicciones.json",
        Path(__file__).resolve().parents[3] / "contracts" / "jurisdicciones.json",
        Path("contracts/jurisdicciones.json").resolve(),
    ]
    for ruta in candidatos:
        if ruta.exists():
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
                            plazo_min=item.get("plazo_min", 6),
                            plazo_max=item.get("plazo_max", 60),
                            dti_maximo=Decimal(str(item.get("dti_maximo", "0.40"))),
                        )
                    )
                return resultado
            except Exception:
                break
    return _J_DEFAULT


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
