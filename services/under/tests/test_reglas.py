"""Las reglas de la decisión de suscripción (HU-W03, CA-W03-07 y CA-W03-09)."""

from decimal import Decimal

import pytest

from app.application.reglas import (
    APROBADA,
    COBERTURA_ALTA,
    KYC_NO_DISPONIBLE,
    KYC_RECHAZADO,
    MONEDA_NO_SOPORTADA,
    RECHAZADA,
    REVISION_MANUAL,
    RIESGO_ALTO,
    RIESGO_DESCONOCIDO,
    decidir,
)

TOPE = Decimal("500000000")


def _decidir(kyc="aprobada", riesgo="BAJO", cobertura="200000000", moneda="COP"):
    return decidir(kyc, riesgo, Decimal(cobertura), moneda, TOPE)


@pytest.mark.parametrize("riesgo", ["BAJO", "MEDIO", "bajo", " medio "])
def test_con_kyc_aprobado_riesgo_bajo_o_medio_y_cobertura_dentro_del_tope_se_aprueba(
    riesgo,
) -> None:
    resultado = _decidir(riesgo=riesgo)

    assert resultado.decision == APROBADA
    assert resultado.motivos == []


def test_la_cobertura_justo_en_el_tope_se_aprueba() -> None:
    assert _decidir(cobertura="500000000").decision == APROBADA


def test_la_cobertura_por_encima_del_tope_va_a_revision() -> None:
    resultado = _decidir(cobertura="500000001")

    assert resultado.decision == REVISION_MANUAL
    assert resultado.motivos == [COBERTURA_ALTA]


def test_riesgo_alto_va_a_revision_y_no_se_rechaza() -> None:
    resultado = _decidir(riesgo="ALTO")

    assert resultado.decision == REVISION_MANUAL
    assert resultado.motivos == [RIESGO_ALTO]


@pytest.mark.parametrize("riesgo", [None, "", "EXTREMO"])
def test_sin_un_nivel_de_riesgo_conocido_no_se_aprueba_sola(riesgo) -> None:
    resultado = _decidir(riesgo=riesgo)

    assert resultado.decision == REVISION_MANUAL
    assert resultado.motivos == [RIESGO_DESCONOCIDO]


def test_el_kyc_rechazado_rechaza_la_suscripcion() -> None:
    resultado = _decidir(kyc="rechazada")

    assert resultado.decision == RECHAZADA
    assert resultado.motivos == [KYC_RECHAZADO]


def test_el_kyc_rechazado_manda_aunque_el_riesgo_y_la_cobertura_tambien_fallen() -> None:
    resultado = _decidir(kyc="rechazada", riesgo="ALTO", cobertura="900000000")

    assert resultado.decision == RECHAZADA
    assert resultado.motivos == [KYC_RECHAZADO]


def test_si_el_kyc_no_responde_va_a_revision_y_no_se_rechaza() -> None:
    resultado = _decidir(kyc="pendiente")

    assert resultado.decision == REVISION_MANUAL
    assert resultado.motivos == [KYC_NO_DISPONIBLE]


def test_una_moneda_distinta_a_cop_va_a_revision() -> None:
    resultado = _decidir(moneda="USD")

    assert resultado.decision == REVISION_MANUAL
    assert resultado.motivos == [MONEDA_NO_SOPORTADA]


def test_los_motivos_se_acumulan_en_revision() -> None:
    resultado = _decidir(kyc="pendiente", riesgo="ALTO", cobertura="900000000")

    assert resultado.decision == REVISION_MANUAL
    assert resultado.motivos == [KYC_NO_DISPONIBLE, RIESGO_ALTO, COBERTURA_ALTA]
