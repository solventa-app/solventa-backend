"""Utilidades de dinero multimoneda y cálculo financiero (HU-W02 / KAN-28).

Todo el cálculo monetario se realiza con Decimal con precisión estricta (nunca float).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, getcontext
from typing import Final

getcontext().prec = 28

# Decimales oficiales por moneda (ISO 4217)
DECIMALES_MONEDA: Final[dict[str, int]] = {
    "COP": 2,
    "MXN": 2,
    "BRL": 2,
    "CLP": 0,
    "PEN": 2,
    "ARS": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CNY": 2,
    "JPY": 0,
}

# Tasas de cambio de referencia (USD por unidad de moneda local)
TASAS_FX_USD: Final[dict[str, Decimal]] = {
    "USD": Decimal("1.0"),
    "COP": Decimal("0.00025"),  # 1 USD = 4,000 COP
    "MXN": Decimal("0.055"),  # 1 USD = 18.18 MXN
    "BRL": Decimal("0.18"),  # 1 USD = 5.55 BRL
    "CLP": Decimal("0.00105"),  # 1 USD = 952.38 CLP
    "PEN": Decimal("0.27"),  # 1 USD = 3.70 PEN
    "EUR": Decimal("1.08"),  # 1 EUR = 1.08 USD
    "GBP": Decimal("1.27"),  # 1 GBP = 1.27 USD
}

UNO: Final[Decimal] = Decimal("1")
DOCE: Final[Decimal] = Decimal("12")


def redondear(monto: Decimal, moneda: str) -> Decimal:
    """Redondea el monto al número de decimales oficial según ISO 4217."""
    decimales = DECIMALES_MONEDA.get(moneda.upper(), 2)
    exp = Decimal(1).scaleb(-decimales)
    return monto.quantize(exp, rounding=ROUND_HALF_UP)


def truncar_a_paso(monto: Decimal, paso: Decimal) -> Decimal:
    """Redondea hacia abajo a un múltiplo comercial (p. ej. COP 10.000, USD 50)."""
    if paso <= 0:
        return monto
    return (monto / paso).to_integral_value(rounding=ROUND_DOWN) * paso


def ea_a_mensual(tasa_ea: Decimal) -> Decimal:
    """Convierte tasa efectiva anual (EA) a tasa efectiva mensual equivalente."""
    return (UNO + tasa_ea) ** (UNO / DOCE) - UNO


def cuota_francesa(principal: Decimal, tasa_mensual: Decimal, n: int) -> Decimal:
    """Calcula la cuota fija mensual bajo el sistema de amortización francés."""
    if n <= 0:
        raise ValueError("El número de periodos n debe ser mayor a 0")
    if tasa_mensual == Decimal("0"):
        return principal / Decimal(n)
    return principal * tasa_mensual / (UNO - (UNO + tasa_mensual) ** -n)


def valor_presente(cuota: Decimal, tasa_mensual: Decimal, n: int) -> Decimal:
    """Calcula el monto máximo que se puede prestar dada una cuota máxima."""
    if n <= 0:
        raise ValueError("El número de periodos n debe ser mayor a 0")
    if tasa_mensual == Decimal("0"):
        return cuota * Decimal(n)
    return cuota * (UNO - (UNO + tasa_mensual) ** -n) / tasa_mensual


def convertir_moneda(monto: Decimal, origen: str, destino: str) -> Decimal:
    """Convierte un monto entre dos monedas usando tasas FX y redondea según ISO 4217."""
    origen_norm = origen.upper()
    destino_norm = destino.upper()

    if origen_norm == destino_norm:
        return redondear(monto, destino_norm)

    tasa_origen = TASAS_FX_USD.get(origen_norm)
    tasa_destino = TASAS_FX_USD.get(destino_norm)

    if tasa_origen is None or tasa_destino is None:
        raise ValueError(f"Tasa de cambio no soportada para {origen_norm} -> {destino_norm}")

    monto_usd = monto * tasa_origen
    monto_destino = monto_usd / tasa_destino
    return redondear(monto_destino, destino_norm)
