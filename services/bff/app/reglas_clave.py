"""Regla de composición de la clave en altas (T17), para validarla en el BFF **antes** de gastar el
código OTP. Copia de `faltantes_de_clave` y `mensaje_clave_debil` de `services/auth/app/usuarios.py`
(AUTH sigue siendo quien la exige al crear la cuenta); los dos lados prueban los mismos casos."""

import unicodedata

CLAVE_LONGITUD_MINIMA = 8
CLAVE_LONGITUD_MAXIMA = 128


def faltantes_de_clave(clave: str) -> list[str]:
    """Requisitos de composición que no cumple la clave (T17). Global y con Unicode: mayúscula =
    categoría `Lu` (Ñ, Á, Ç, Ł...), minúscula = `Ll` (ñ, é, ß...), especial = cualquier carácter
    que no sea letra ni número (`¿`, `€`, `_`, espacio, emoji...). Las letras sin caso (chino,
    árabe, hindi) cuentan como letras pero no como mayúscula ni minúscula."""
    categorias = [unicodedata.category(c) for c in clave]
    faltan = []
    if "Lu" not in categorias:
        faltan.append("una mayúscula")
    if "Ll" not in categorias:
        faltan.append("una minúscula")
    if not any(c[0] not in "LN" for c in categorias):
        faltan.append("un carácter especial")
    return faltan


def mensaje_clave_debil(faltan: list[str]) -> str:
    lista = faltan[0] if len(faltan) == 1 else ", ".join(faltan[:-1]) + " y " + faltan[-1]
    return f"La clave no es segura: falta {lista}."


def motivo_clave_invalida(clave: str) -> str | None:
    """Mensaje apto para el cliente si la clave no sirve para un alta; `None` si cumple."""
    if not CLAVE_LONGITUD_MINIMA <= len(clave) <= CLAVE_LONGITUD_MAXIMA:
        return (
            f"La clave debe tener entre {CLAVE_LONGITUD_MINIMA} y {CLAVE_LONGITUD_MAXIMA} "
            "caracteres."
        )
    faltan = faltantes_de_clave(clave)
    return mensaje_clave_debil(faltan) if faltan else None
