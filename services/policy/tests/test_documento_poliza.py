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

    assert "Con la cuota de tu crédito" in texto
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


# ---------- KAN-290: póliza de auto ----------

PROPUESTA_AUTO = {
    "producto": "AUTO",
    "prima": {"valor": "332198.00", "moneda": "COP"},
    "cobertura": {"valor": "118500000.00", "moneda": "COP"},
    "plazo_meses": 12,
    "vehiculo": {
        "placaEnmascarada": "ABC•••",
        "marca": "MAZDA",
        "linea": "3 GRAND TOURING",
        "modelo": 2024,
        "uso": "PARTICULAR",
        "ciudadCirculacion": "Bogotá",
    },  # fmt: skip
    "coberturas": [
        {
            "tipo": "RESPONSABILIDAD_CIVIL",
            "valorAsegurado": {"valor": "1000000000", "moneda": "COP"},
            "deducible": None,
        },
        {
            "tipo": "PERDIDA_PARCIAL_DANOS",
            "valorAsegurado": {"valor": "118500000", "moneda": "COP"},
            "deducible": "10 % mín. 1 SMMLV",
        },
        {"tipo": "ASISTENCIA", "valorAsegurado": None, "deducible": None},
    ],  # fmt: skip
}


def test_la_poliza_de_auto_lleva_el_vehiculo_y_las_coberturas() -> None:
    texto = _pdf(propuesta=PROPUESTA_AUTO).decode("latin-1")
    for esperado in (
        "Seguro Vehicular",
        "MAZDA 3 GRAND TOURING",
        "ABC***",
        "Responsabilidad civil extracontractual",
        "deducible 10 % m",
        "conductor elegido",
    ):
        assert esperado in texto
    assert "Crédito asegurado".encode("latin-1").decode("latin-1") not in texto
    assert "Plazo del cr" not in texto


def test_el_contrato_borrador_de_auto_habla_del_seguro_de_autos() -> None:
    from app.application.documento_poliza import generar_borrador

    texto = generar_borrador(PROPUESTA_AUTO).decode("latin-1")
    assert "seguro vehicular" in texto and "Coberturas" in texto
    assert "cr\xe9dito hipotecario" not in texto


def test_el_contrato_lleva_las_condiciones_generales_numeradas_y_el_pie_de_pagina() -> None:
    from app.application.documento_poliza import generar_borrador

    texto = generar_borrador(PROPUESTA_AUTO, ambiente_pruebas=False).decode("latin-1")
    assert "Condiciones generales" in texto
    assert "1. Objeto del seguro" in texto
    assert "artículo 1068 del Código de Comercio" in texto  # mora: terminación automática
    assert "Cobertura paramétrica de clima" in texto  # solo en auto
    assert "Página 1 de" in texto
    assert "ambiente de pruebas" not in texto


# ---------- KAN-26: títulos con el producto, idioma y moneda ----------

USD = {"idioma": "es", "moneda": "USD", "cop_por_unidad": "4000", "fecha_tasa": "2026-11-20"}


def _titulo_interno(pdf: bytes) -> str:
    """El `/Title` de los metadatos: texto Latin-1 entre paréntesis o UTF-16 en hexadecimal."""
    import re  # noqa: PLC0415

    hexa = re.search(rb"/Title <feff([0-9a-f]+)>", pdf)
    if hexa:
        return bytes.fromhex(hexa.group(1).decode()).decode("utf-16-be")
    plano = re.search(rb"/Title \((.*?)\)\n", pdf)
    assert plano is not None
    return plano.group(1).replace(b"\\", b"").decode("latin-1")


def test_los_titulos_llevan_el_producto_en_espanol() -> None:
    from app.application.documento_poliza import generar_borrador, generar_contrato_firmado

    poliza = _pdf(ambiente_pruebas=False)
    texto = poliza.decode("latin-1")
    assert "Póliza de Seguro de Vida Hipotecario No. SOL-2026-000001" in texto
    assert "en trámite" not in texto
    borrador = generar_borrador(PROPUESTA_AUTO, ambiente_pruebas=False).decode("latin-1")
    assert "Contrato de Seguro Vehicular" in borrador
    firmado = generar_contrato_firmado(
        {**PROPUESTA, "cliente_id": "c" * 20}, {**EVIDENCIA, "version_documento": "v1"}
    ).decode("latin-1")
    assert "Firmado el 22/10/2026" in firmado


def test_el_titulo_interno_lleva_el_producto_y_sus_tildes() -> None:
    from app.application.documento_poliza import Presentacion, generar_borrador

    assert _titulo_interno(_pdf()) == "Póliza SOL-2026-000001 · Seguro de Vida Hipotecario"
    assert _titulo_interno(generar_borrador(PROPUESTA_AUTO)) == (
        "Contrato de Seguro Vehicular (borrador)"
    )
    en_ingles = generar_borrador(PROPUESTA, presentacion=Presentacion.desde({"idioma": "en"}))
    assert _titulo_interno(en_ingles) == "Mortgage Life Insurance Contract (draft)"


def test_en_ingles_todo_el_documento_sale_en_ingles() -> None:
    from app.application.documento_poliza import Presentacion, generar_borrador

    texto = generar_borrador(
        PROPUESTA_AUTO, ambiente_pruebas=False, presentacion=Presentacion.desde({"idioma": "en"})
    ).decode("latin-1")
    for esperado in (
        "Vehicle Insurance Contract",
        "Insured vehicle",
        "Third-party liability",
        "General terms and conditions",
        "article 1068 of the Colombian Commercial Code",
        "Page 1 of",
        "$1,000,000,000 COP",
    ):
        assert esperado in texto
    for no_esperado in ("Condiciones generales", "Vehículo asegurado", "Página"):
        assert no_esperado not in texto


def test_en_otra_moneda_cada_monto_lleva_su_equivalente_en_cop_y_la_tasa() -> None:
    from app.application.documento_poliza import Presentacion

    texto = _pdf(presentacion=Presentacion.desde(USD)).decode("latin-1")
    assert "$ 46,25 USD" in texto  # 185.000 COP / 4.000
    assert "Equivale a $ 185.000 COP" in texto
    assert "tasa de referencia del 20/11/2026: 1 USD = $ 4.000 COP" in texto
    assert "se cobra en COP" in texto

    en_ingles = _pdf(presentacion=Presentacion.desde({**USD, "idioma": "en"})).decode("latin-1")
    assert "$46.25 USD" in en_ingles and "Equivalent to $185,000 COP" in en_ingles


def test_en_cop_no_hay_equivalencia_ni_tasa() -> None:
    texto = _pdf().decode("latin-1")
    assert "Equivale a" not in texto and "tasa de referencia" not in texto


def test_la_presentacion_tolera_datos_raros_y_cae_en_espanol_y_cop() -> None:
    from app.application.documento_poliza import Presentacion

    assert Presentacion.desde(None) == Presentacion()
    assert Presentacion.desde({"idioma": "fr", "moneda": "USD"}) == Presentacion()  # sin tasa
    assert Presentacion.desde({"moneda": "XYZ", "cop_por_unidad": "1"}).moneda == "COP"
    assert Presentacion.desde({"moneda": "USD", "cop_por_unidad": "no"}).moneda == "COP"
    assert Presentacion.desde({"idioma": "EN-us"}).idioma == "en"
    assert Presentacion.desde({"moneda": "CLP", "cop_por_unidad": "4"}).convierte
