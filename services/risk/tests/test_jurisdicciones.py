"""Pruebas unitarias para catálogo de jurisdicciones y validación de documentos."""

import random
from decimal import Decimal

import pytest

from app.domain import documentos as doc
from app.domain.jurisdicciones import JURISDICCIONES, listar_jurisdicciones, obtener_jurisdiccion


def test_catalogo_jurisdicciones_contiene_paises_requeridos():
    paises_requeridos = {"CO", "MX", "BR", "CL", "PE", "US", "ES"}
    assert paises_requeridos.issubset(set(JURISDICCIONES.keys()))

    lista = listar_jurisdicciones()
    codigos_lista = {j.codigo for j in lista}
    assert paises_requeridos.issubset(codigos_lista)


def test_obtener_jurisdiccion_valida_e_invalida():
    co = obtener_jurisdiccion("CO")
    assert co.codigo == "CO"
    assert co.nombre == "Colombia"
    assert co.moneda == "COP"
    assert co.tipo_documento == "CC"
    assert co.region_datos == "latam"
    assert co.monto_min == Decimal("500000")
    assert co.monto_max == Decimal("80000000")
    assert co.plazo_min == 6
    assert co.plazo_max == 60
    assert co.dti_maximo == Decimal("0.40")

    # Minúsculas deben resolverse correctamente
    assert obtener_jurisdiccion("co").codigo == "CO"
    assert obtener_jurisdiccion("mx").codigo == "MX"

    with pytest.raises(ValueError, match="no soportado"):
        obtener_jurisdiccion("ZZ")


def test_propiedades_consistentes_en_todas_las_jurisdicciones():
    for cod, j in JURISDICCIONES.items():
        assert j.codigo == cod
        assert len(j.moneda) == 3
        assert j.monto_min > 0
        assert j.monto_max > j.monto_min
        assert j.plazo_min > 0
        assert j.plazo_max >= j.plazo_min
        assert Decimal("0") < j.dti_maximo <= Decimal("1")
        assert len(j.regimen_open_finance) > 0
        assert j.region_datos in {"latam", "us", "eu", "uk", "cn"}


def test_colombia_cc_valida_e_invalida():
    val = obtener_jurisdiccion("CO").validar_documento
    assert val("1020304050") == "1020304050"
    assert val("1.020.304.050") == "1020304050"
    assert val("80123456") == "80123456"

    # Inválidas: empieza con 0, muy corta (<6), muy larga (>10)
    with pytest.raises(ValueError):
        val("012345")
    with pytest.raises(ValueError):
        val("12345")
    with pytest.raises(ValueError):
        val("12345678901")

    # Generación aleatoria
    rng = random.Random(42)
    generado = obtener_jurisdiccion("CO").generar_documento(rng)
    assert val(generado) == generado


def test_mexico_curp_valida_e_invalida():
    val = obtener_jurisdiccion("MX").validar_documento
    rng = random.Random(123)
    for _ in range(5):
        curp = obtener_jurisdiccion("MX").generar_documento(rng)
        assert val(curp) == curp

    # Formato inválido
    with pytest.raises(ValueError):
        val("CURP_INVALIDA")
    with pytest.raises(ValueError):
        val("ABCD900101ZDFRRN09")  # sexo inválido (no es H, M ni X)


def test_brasil_cpf_valida_e_invalida():
    val = obtener_jurisdiccion("BR").validar_documento
    rng = random.Random(456)
    for _ in range(5):
        cpf = obtener_jurisdiccion("BR").generar_documento(rng)
        assert val(cpf) == cpf

    # Dígitos repetidos inválidos
    with pytest.raises(ValueError):
        val("11111111111")
    with pytest.raises(ValueError):
        val("00000000000")
    # Dígito verificador incorrecto
    with pytest.raises(ValueError):
        val("12345678900")
    # Longitud incorrecta
    with pytest.raises(ValueError):
        val("123456")


def test_chile_rut_valida_e_invalida():
    val = obtener_jurisdiccion("CL").validar_documento
    rng = random.Random(789)
    for _ in range(5):
        rut = obtener_jurisdiccion("CL").generar_documento(rng)
        assert val(rut) == rut

    # Con puntos y guion
    rut_base = obtener_jurisdiccion("CL").generar_documento(random.Random(10))
    cuerpo, dv = rut_base.split("-")
    assert val(f"{cuerpo}-{dv}") == rut_base

    # Dígito verificador erróneo
    dv_invalido = "0" if dv != "0" else "1"
    with pytest.raises(ValueError):
        val(f"{cuerpo}-{dv_invalido}")

    # Longitud errónea
    with pytest.raises(ValueError):
        val("123-4")


def test_peru_dni_valida_e_invalida():
    val = obtener_jurisdiccion("PE").validar_documento
    rng = random.Random(101)
    for _ in range(5):
        dni = obtener_jurisdiccion("PE").generar_documento(rng)
        assert val(dni) == dni
        assert len(dni) == 8

    # Menos o más de 8 dígitos
    with pytest.raises(ValueError):
        val("1234567")
    with pytest.raises(ValueError):
        val("123456789")


def test_estados_unidos_ssn_valida_e_invalida():
    val = obtener_jurisdiccion("US").validar_documento
    rng = random.Random(202)
    for _ in range(5):
        ssn = obtener_jurisdiccion("US").generar_documento(rng)
        assert val(ssn) == ssn
        assert len(ssn) == 9

    # Áreas inválidas
    with pytest.raises(ValueError):
        val("000123456")
    with pytest.raises(ValueError):
        val("666123456")
    with pytest.raises(ValueError):
        val("900123456")
    # Grupo 00
    with pytest.raises(ValueError):
        val("123004567")
    # Serie 0000
    with pytest.raises(ValueError):
        val("123450000")


def test_espana_dni_nie_valida_e_invalida():
    val = obtener_jurisdiccion("ES").validar_documento
    rng = random.Random(303)
    for _ in range(5):
        dni = obtener_jurisdiccion("ES").generar_documento(rng)
        assert val(dni) == dni

    # Letra de control errónea
    with pytest.raises(ValueError):
        val("12345678A")  # Su letra calculada no es A

    # Formato inválido
    with pytest.raises(ValueError):
        val("12345")


def test_algoritmo_luhn():
    assert doc.validar_luhn("79927398713") is True
    assert doc.validar_luhn("49927398716") is True
    assert doc.validar_luhn("79927398710") is False
    assert doc.validar_luhn("49927398717") is False
    assert doc.validar_luhn("") is False


def test_enmascarar_documentos():
    assert doc.enmascarar("1020304050") == "******4050"
    assert doc.enmascarar("1234") == "**1234"
    assert doc.enmascarar("123456789") == "*****6789"
