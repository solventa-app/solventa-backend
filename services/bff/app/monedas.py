"""Conversión de montos a la moneda que el usuario eligió en la web (KAN-209).

La prima se sigue calculando en COP (RATING no cambia). Aquí solo se *muestra* en otra
moneda: el BFF pide la tabla de tasas al ACL Worker (`GET /tasas-cambio`, regla #2) y
convierte con `Decimal`, nunca con float, redondeando a los decimales ISO 4217 de la moneda
destino (ver `docs/evidencias/diseno-i18n-moneda-prima.md`, sección 3)."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# Decimales ISO 4217 de las monedas que ofrece el selector del header de la web.
DECIMALES: dict[str, int] = {
    "COP": 2,
    "USD": 2,
    "EUR": 2,
    "MXN": 2,
    "BRL": 2,
    "PEN": 2,
    "CLP": 0,
}
MAXIMO_MONTOS = 50
# Tasas fijas para el modo prueba de socios, que nunca llama a los micros ni a los stubs (W07-10).
TASAS_PRUEBA: dict[str, str] = {
    "COP": "1",
    "USD": "4100",
    "EUR": "4450",
    "MXN": "225",
    "BRL": "750",
    "PEN": "1100",
    "CLP": "4.4",
}

MENSAJE_MONEDA_NO_SOPORTADA = "Moneda no soportada."
MENSAJE_MONTO_INVALIDO = "El monto no es válido."
MENSAJE_DEMASIADOS_MONTOS = "Se pueden convertir hasta 50 montos a la vez."
MENSAJE_TASAS_NO_DISPONIBLES = "No pudimos convertir los montos en este momento."


class ConversionInvalida(Exception):
    """Moneda o monto que no se puede convertir. Mensaje apto para el usuario."""


def convertir(valor: str, origen: str, destino: str, cop_por_unidad: dict[str, str]) -> str:
    """`valor` en `origen` expresado en `destino`, como texto decimal."""
    if origen not in cop_por_unidad or destino not in cop_por_unidad or destino not in DECIMALES:
        raise ConversionInvalida(MENSAJE_MONEDA_NO_SOPORTADA)
    try:
        cantidad = Decimal(valor)
    except InvalidOperation as error:
        raise ConversionInvalida(MENSAJE_MONTO_INVALIDO) from error
    if not cantidad.is_finite():
        raise ConversionInvalida(MENSAJE_MONTO_INVALIDO)
    if origen == destino:
        resultado = cantidad
    else:
        resultado = cantidad * Decimal(cop_por_unidad[origen]) / Decimal(cop_por_unidad[destino])
    paso = Decimal(1).scaleb(-DECIMALES[destino])
    return str(resultado.quantize(paso, rounding=ROUND_HALF_EVEN))
