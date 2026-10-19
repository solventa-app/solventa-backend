from decimal import Decimal

from app.domain.money import (
    convertir_moneda,
    redondear,
    truncar_a_paso,
)


def test_redondear_monedas_risk():
    assert redondear(Decimal("1234.567"), "COP") == Decimal("1234.57")
    assert redondear(Decimal("1234.56"), "CLP") == Decimal("1235")
    assert redondear(Decimal("99.999"), "USD") == Decimal("100.00")


def test_truncar_a_paso_risk():
    assert truncar_a_paso(Decimal("148500"), Decimal("10000")) == Decimal("140000")


def test_convertir_moneda_risk():
    cop_a_usd = convertir_moneda(Decimal("4000000"), "COP", "USD")
    assert cop_a_usd == Decimal("1000.00")
