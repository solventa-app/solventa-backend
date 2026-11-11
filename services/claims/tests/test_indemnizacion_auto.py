"""KAN-290: la indemnización de un siniestro de la póliza de auto (decisión A-3 del plan)."""

import pytest

from app.application.servicio_siniestros import (
    MENSAJE_SIN_INDEMNIZACION,
    MENSAJE_VALOR_DANO,
    ErrorNegocio,
    indemnizacion_auto,
)

VALOR = {"valor": "118500000.00", "moneda": "COP"}
RC = {"valor": "1000000000.00", "moneda": "COP"}
DEDUCIBLE = "10 % mín. 1 SMMLV"
POLIZA = {
    "producto": "AUTO",
    "cobertura": VALOR,
    "vehiculo": {"deducible": "DIEZ_POR_CIENTO"},
    "coberturas": [
        {"tipo": "RESPONSABILIDAD_CIVIL", "valorAsegurado": RC, "deducible": None},
        {"tipo": "PERDIDA_TOTAL_DANOS", "valorAsegurado": VALOR, "deducible": DEDUCIBLE},
        {"tipo": "PERDIDA_PARCIAL_DANOS", "valorAsegurado": VALOR, "deducible": DEDUCIBLE},
        {"tipo": "PERDIDA_TOTAL_HURTO", "valorAsegurado": VALOR, "deducible": DEDUCIBLE},
        {"tipo": "PERDIDA_PARCIAL_HURTO", "valorAsegurado": VALOR, "deducible": DEDUCIBLE},
    ],
}  # fmt: skip


def test_perdida_total_es_el_valor_asegurado_menos_el_deducible() -> None:
    # 118.500.000 − 10 % (11.850.000) = 106.650.000
    assert indemnizacion_auto("PERDIDA_TOTAL_DANOS", POLIZA, None) == {
        "valor": "106650000.00",
        "moneda": "COP",
    }
    assert indemnizacion_auto("HURTO_TOTAL", POLIZA, "999")["valor"] == "106650000.00"


def test_parcial_usa_el_valor_del_dano_con_el_minimo_de_un_smmlv() -> None:
    # 8.000.000: el 10 % (800.000) es menor que 1 SMMLV (1.423.500) → 6.576.500
    assert indemnizacion_auto("CHOQUE_PARCIAL", POLIZA, "8000000")["valor"] == "6576500.00"
    # 30.000.000: el 10 % (3.000.000) supera el SMMLV → 27.000.000
    assert indemnizacion_auto("HURTO_PARCIAL", POLIZA, "30000000")["valor"] == "27000000.00"


def test_el_dano_se_acota_al_valor_asegurado() -> None:
    assert indemnizacion_auto("CHOQUE_PARCIAL", POLIZA, "500000000")["valor"] == "106650000.00"


def test_rc_no_tiene_deducible_y_su_tope_es_el_de_la_rc() -> None:
    assert indemnizacion_auto("RESPONSABILIDAD_CIVIL", POLIZA, "5600000")["valor"] == "5600000.00"


def test_sin_deducible_paga_la_perdida_completa() -> None:
    poliza = {**POLIZA, "vehiculo": {"deducible": "SIN_DEDUCIBLE"}}
    assert indemnizacion_auto("CHOQUE_PARCIAL", poliza, "8000000")["valor"] == "8000000.00"


CASOS_SIN_PAGO = [
    (None, MENSAJE_VALOR_DANO),
    ("0", MENSAJE_VALOR_DANO),
    ("900000", MENSAJE_SIN_INDEMNIZACION),
]


@pytest.mark.parametrize(("valor", "mensaje"), CASOS_SIN_PAGO)
def test_parcial_sin_valor_o_por_debajo_del_deducible_no_se_aprueba(valor, mensaje) -> None:
    with pytest.raises(ErrorNegocio, match=mensaje):
        indemnizacion_auto("CHOQUE_PARCIAL", POLIZA, valor)
