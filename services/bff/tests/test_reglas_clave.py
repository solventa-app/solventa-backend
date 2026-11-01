"""Regla de la clave en altas (T17): mismos casos que `services/auth/tests/test_clave_segura.py`."""

import pytest

from app.idioma import traducir
from app.reglas_clave import motivo_clave_invalida


@pytest.mark.parametrize(
    "clave",
    [
        "Contraseña¿1",  # español: ñ minúscula, ¿ especial
        "Ñandú€uno",  # ñ/Ñ/ú, € especial
        "Größe-Ärger1",  # alemán: ß, ö, Ä
        "Zażółć Gęślą",  # polaco: Ż, ł, espacio especial
        "Açaí_Brasil",  # portugués: Ç/ç, guion bajo especial
        "Abcdefg😀",  # emoji como especial
    ],
)
def test_claves_validas(clave: str) -> None:
    assert motivo_clave_invalida(clave) is None


@pytest.mark.parametrize(
    ("clave", "mensaje"),
    [
        ("clave1234", "La clave no es segura: falta una mayúscula y un carácter especial."),
        ("CLAVE1234!", "La clave no es segura: falta una minúscula."),
        ("abcdefgh", "La clave no es segura: falta una mayúscula y un carácter especial."),
        ("Abcdefg1", "La clave no es segura: falta un carácter especial."),
        # Letras sin mayúscula ni minúscula: cuentan como letras, no como casos.
        ("日本語のパスワード!", "La clave no es segura: falta una mayúscula y una minúscula."),
        ("corta", "La clave debe tener entre 8 y 128 caracteres."),
    ],
)
def test_claves_invalidas(clave: str, mensaje: str) -> None:
    assert motivo_clave_invalida(clave) == mensaje


def test_la_longitud_cuenta_caracteres_no_bytes() -> None:
    assert motivo_clave_invalida("Ñ" * 64 + "ñ!" + "a" * 62) is None  # 128 caracteres
    assert motivo_clave_invalida("Ñ" * 64 + "ñ!" + "a" * 63) is not None  # 129


def test_traduccion_al_ingles() -> None:
    texto = "La clave no es segura: falta una mayúscula, una minúscula y un carácter especial."
    assert traducir(texto, "en") == (
        "The password isn't secure: it is missing an uppercase letter, a lowercase letter "
        "and a special character."
    )
