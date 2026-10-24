"""El PDF de la póliza (HU-W03, KAN-215, CA-W03-08)."""

from datetime import UTC, datetime

import pytest

from app.application.documento_poliza import (
    AVISO_AMBIENTE_PRUEBAS,
    formatear_monto,
    generar_pdf,
)

POLIZA = {
    "id": "pz-1",
    "cliente_id": "abcdef0123456789abcdef",
    "estado": "EMITIDA",
    "vigencia_desde": datetime(2026, 10, 22, tzinfo=UTC),
    "vigencia_hasta": datetime(2031, 10, 22, tzinfo=UTC),
}
PROPUESTA = {
    "prima": {"valor": "185000", "moneda": "COP"},
    "cobertura": {"valor": "200000000", "moneda": "COP"},
    "plazo_meses": 60,
}
EVIDENCIA = {
    "referencia_proveedor": "fe-123",
    "firmada_en": datetime(2026, 10, 22, 17, 30, tzinfo=UTC),
    "documento_hash": "a" * 64,
}


def _pdf(**cambios) -> bytes:
    argumentos = {
        "poliza": POLIZA, "propuesta": PROPUESTA, "evidencia": EVIDENCIA,
        "numero": "SOL-2026-000001", "asegurado": "Ana María Pérez", "ambiente_pruebas": True,
    }  # fmt: skip
    argumentos.update(cambios)
    return generar_pdf(**argumentos)


def test_es_un_pdf_de_una_pagina() -> None:
    pdf = _pdf()

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert b"/Count 1" in pdf  # el árbol de páginas declara una sola


@pytest.mark.parametrize(
    "texto",
    [
        b"SOLVENTA",
        b"SOL-2026-000001",
        b"60 meses",
        b"$ 200.000.000 COP",  # valor asegurado
        b"$ 185.000 COP",  # prima mensual
        b"22/10/2031",  # fin de la vigencia
        b"fe-123",  # referencia de la firma
        b"22/10/2026 17:30 UTC",
    ],
)
def test_lleva_los_datos_de_la_poliza(texto: bytes) -> None:
    assert texto in _pdf()


def test_lleva_la_huella_del_documento_firmado() -> None:
    assert ("a" * 64).encode() in _pdf()


def test_con_el_nombre_lo_usa_y_sin_el_identifica_por_la_referencia_opaca() -> None:
    con_nombre = _pdf(asegurado="Ana Maria Perez")
    sin_nombre = _pdf(asegurado=None)

    assert b"Ana Maria Perez" in con_nombre
    assert b"abcdef012345" in sin_nombre  # los primeros 12 caracteres del cliente opaco
    assert b"Ana Maria" not in sin_nombre


def test_el_nombre_en_blanco_se_trata_como_ausente() -> None:
    assert b"abcdef012345" in _pdf(asegurado="   ")


def test_un_nombre_con_caracteres_fuera_de_latin1_no_rompe_el_documento() -> None:
    pdf = _pdf(asegurado="Zoë 😀 Ωmega")

    assert pdf.startswith(b"%PDF-")


def test_en_ambiente_de_pruebas_avisa_que_no_tiene_validez() -> None:
    assert b"ambiente de pruebas" in _pdf(ambiente_pruebas=True)
    assert b"ambiente de pruebas" not in _pdf(ambiente_pruebas=False)
    assert "no tiene validez legal" in AVISO_AMBIENTE_PRUEBAS
    assert "validez" in _pdf(ambiente_pruebas=True).decode("latin-1")


def test_el_texto_fijo_lleva_sus_tildes() -> None:
    texto = _pdf().decode("latin-1")

    assert "con la cuota de tu crédito" in texto
    assert "firmó" in texto
    assert "electrónica" in texto


def test_el_pdf_no_lleva_el_cliente_completo_ni_el_correo() -> None:
    pdf = _pdf(asegurado=None)

    assert POLIZA["cliente_id"].encode() not in pdf  # solo los primeros 12 caracteres


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("185000", "$ 185.000 COP"),
        ("200000000", "$ 200.000.000 COP"),
        ("185000.50", "$ 185.000,50 COP"),
        ("0", "$ 0 COP"),
        ("1234567.00", "$ 1.234.567 COP"),
    ],
)
def test_formatea_el_monto_con_puntos_de_miles(valor: str, esperado: str) -> None:
    assert formatear_monto(valor, "COP") == esperado


def test_un_monto_que_no_es_decimal_se_muestra_tal_cual_sin_romper() -> None:
    assert formatear_monto("mucho", "COP") == "mucho COP"
