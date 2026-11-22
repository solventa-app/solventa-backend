"""Montos del push en la moneda y el idioma del cliente (KAN-259, sobre KAN-209).

El productor del evento manda el monto crudo (texto decimal, p. ej. «500000.00») y su moneda. Aquí
se convierte a la moneda que el cliente eligió (preferencia de AUTH) con la tabla de tasas del ACL
Worker (`GET /tasas-cambio`, regla #2) y se escribe como se lee en su idioma. Es solo para mostrar:
lo que se paga no cambia. Si no hay tasas, el push sale en la moneda original (regla #4)."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# Decimales con los que se lee cada moneda en un mensaje corto (el peso, sin centavos).
DECIMALES_VISIBLES: dict[str, int] = {
    "COP": 0,
    "CLP": 0,
    "USD": 2,
    "EUR": 2,
    "MXN": 2,
    "BRL": 2,
    "PEN": 2,
}
# Separadores de miles y de decimales por idioma.
_SEPARADORES: dict[str, tuple[str, str]] = {"es": (".", ","), "en": (",", ".")}


def _decimal(valor: object) -> Decimal | None:
    try:
        numero = Decimal(str(valor).strip())
    except InvalidOperation:
        return None
    return numero if numero.is_finite() else None


def numero_legible(valor: object, idioma: str, decimales: int | None = None) -> str:
    """«500000.00» -> «500.000» (es) o «500,000» (en). Sin `decimales`, los que traiga el número
    (máximo uno, sin ceros de relleno). Lo que no es un número se devuelve tal cual."""
    numero = _decimal(valor)
    if numero is None:
        return str(valor)
    if decimales is None:
        decimales = 0 if numero == numero.to_integral_value() else 1
    paso = Decimal(1).scaleb(-decimales)
    texto = f"{numero.quantize(paso, rounding=ROUND_HALF_EVEN):,.{decimales}f}"
    miles, coma = _SEPARADORES.get(idioma, _SEPARADORES["es"])
    return texto.replace(",", "§").replace(".", coma).replace("§", miles)


def importe_legible(monto: object, moneda: str, idioma: str) -> str:
    """El monto con su moneda, como lo muestra la app: «$ 500.000» y «USD 121,95» en español;
    «COP 500,000» y «USD 121.95» en inglés."""
    numero = numero_legible(monto, idioma, DECIMALES_VISIBLES.get(moneda))
    if moneda == "COP" and idioma == "es":
        return f"$ {numero}"
    return f"{moneda} {numero}".strip()


def convertir(
    valor: object, origen: str, destino: str, cop_por_unidad: dict[str, str]
) -> Decimal | None:
    """`valor` en `origen` expresado en `destino`; `None` si no se puede (moneda sin tasa o monto
    que no es un número), para que el llamador deje el monto original."""
    numero = _decimal(valor)
    tasa_origen = _decimal(cop_por_unidad.get(origen))
    tasa_destino = _decimal(cop_por_unidad.get(destino))
    if numero is None or not tasa_origen or not tasa_destino or tasa_destino <= 0:
        return None
    return numero * tasa_origen / tasa_destino
