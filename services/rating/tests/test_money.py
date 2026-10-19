from decimal import Decimal

import pytest

from app.application.money import (
    convertir_moneda,
    cuota_francesa,
    ea_a_mensual,
    redondear,
    truncar_a_paso,
    valor_presente,
)


def test_redondear_monedas():
    # COP tiene 2 decimales según ISO 4217
    assert redondear(Decimal("1234.567"), "COP") == Decimal("1234.57")
    assert redondear(Decimal("1234.562"), "COP") == Decimal("1234.56")

    # CLP no tiene decimales
    assert redondear(Decimal("1234.56"), "CLP") == Decimal("1235")
    assert redondear(Decimal("1234.40"), "CLP") == Decimal("1234")

    # USD y EUR tienen 2 decimales
    assert redondear(Decimal("99.999"), "USD") == Decimal("100.00")
    assert redondear(Decimal("49.123"), "EUR") == Decimal("49.12")


def test_truncar_a_paso():
    assert truncar_a_paso(Decimal("148500"), Decimal("10000")) == Decimal("140000")
    assert truncar_a_paso(Decimal("57"), Decimal("5")) == Decimal("55")
    assert truncar_a_paso(Decimal("100"), Decimal("0")) == Decimal("100")


def test_ea_a_mensual():
    # 26.82% EA (~2% mensual)
    tasa_ea = Decimal("0.26824179")
    tasa_mensual = ea_a_mensual(tasa_ea)
    assert abs(tasa_mensual - Decimal("0.02")) < Decimal("0.0001")


def test_cuota_francesa_y_valor_presente():
    principal = Decimal("1000000")
    tasa_mensual = Decimal("0.015")  # 1.5% mensual
    n = 12

    cuota = cuota_francesa(principal, tasa_mensual, n)
    assert cuota > 0

    # Invertir la fórmula con valor_presente debe reconstruir el principal
    reconstruido = valor_presente(cuota, tasa_mensual, n)
    assert abs(reconstruido - principal) < Decimal("0.01")


def test_cuota_tasa_cero():
    principal = Decimal("1200000")
    cuota = cuota_francesa(principal, Decimal("0"), 12)
    assert cuota == Decimal("100000")
    assert valor_presente(cuota, Decimal("0"), 12) == principal


def test_cuota_n_invalido():
    with pytest.raises(ValueError, match="mayor a 0"):
        cuota_francesa(Decimal("1000"), Decimal("0.01"), 0)
    with pytest.raises(ValueError, match="mayor a 0"):
        valor_presente(Decimal("100"), Decimal("0.01"), -1)


def test_convertir_moneda():
    # Misma moneda
    assert convertir_moneda(Decimal("100.555"), "COP", "COP") == Decimal("100.56")

    # COP a USD: 4,000,000 COP * 0.00025 = 1,000 USD
    cop_a_usd = convertir_moneda(Decimal("4000000"), "COP", "USD")
    assert cop_a_usd == Decimal("1000.00")

    # USD a COP: 1000 USD / 0.00025 = 4,000,000 COP
    usd_a_cop = convertir_moneda(Decimal("1000"), "USD", "COP")
    assert usd_a_cop == Decimal("4000000.00")

    # Moneda no soportada
    with pytest.raises(ValueError, match="no soportada"):
        convertir_moneda(Decimal("100"), "XYZ", "USD")
