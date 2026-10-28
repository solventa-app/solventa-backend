"""Validación y tokenización de la tarjeta en la página de pago del stub (W03-28).

El stub hace de pasarela (PayU, Stripe): es el único que ve la tarjeta. **Nada de este módulo
escribe en un log** ni devuelve el número, el CVV o el nombre: la tarjeta vive solo en la memoria de
la petición que la valida. Lo que sobrevive es el token (`tok_…`) con la marca, el tipo y los
últimos 4 dígitos, y a Solventa le llegan solo el token, la marca y el tipo.

Tarjetas de prueba al estilo de Stripe: cualquier otra tarjeta válida por Luhn se aprueba."""

import re
import secrets
from dataclasses import dataclass
from datetime import date
from typing import Literal

Resultado = Literal["aprobado", "rechazado", "fallido"]
TipoTarjeta = Literal["CREDITO", "DEBITO"]

# número -> (resultado, motivo). El motivo usa los mismos códigos que el cobro con token.
TARJETAS_PRUEBA: dict[str, tuple[Resultado, str | None]] = {
    "4242424242424242": ("aprobado", None),
    "5555555555554444": ("aprobado", None),
    "4000000000000002": ("rechazado", "fondos_insuficientes"),
    "4000000000000069": ("rechazado", "tarjeta_vencida"),
    "4000000000000119": ("fallido", "error_procesamiento"),
}


class TarjetaInvalida(ValueError):
    """`campo` dice qué corregir (numero, nombre, vencimiento, cvv o tipo); nunca el valor."""

    def __init__(self, campo: str) -> None:
        super().__init__(campo)
        self.campo = campo


@dataclass(frozen=True)
class MedioTokenizado:
    token: str
    marca: str
    tipo: TipoTarjeta
    ultimos4: str  # solo para la pantalla del stub; nunca sale hacia Solventa


def luhn_valido(numero: str) -> bool:
    if not numero.isdigit() or not 12 <= len(numero) <= 19:
        return False
    total = 0
    for posicion, caracter in enumerate(reversed(numero)):
        digito = int(caracter)
        if posicion % 2 == 1:
            digito *= 2
            if digito > 9:
                digito -= 9
        total += digito
    return total % 10 == 0


def marca(numero: str) -> str:
    if numero.startswith("4"):
        return "VISA"
    if re.match(r"^(5[1-5]|2(2[2-9][1-9]|2[3-9]\d|[3-6]\d\d|7[01]\d|720))", numero):
        return "MASTERCARD"
    if numero.startswith(("34", "37")):
        return "AMEX"
    return "OTRA"


def _vencimiento_futuro(texto: str, hoy: date) -> bool:
    coincidencia = re.fullmatch(r"(\d{2})\s*/\s*(\d{2}|\d{4})", texto.strip())
    if not coincidencia:
        return False
    mes, anio = int(coincidencia.group(1)), int(coincidencia.group(2))
    if not 1 <= mes <= 12:
        return False
    if anio < 100:
        anio += 2000
    # Vale hasta el último día del mes impreso.
    return (anio, mes) >= (hoy.year, hoy.month)


def validar(datos: dict, hoy: date) -> tuple[str, TipoTarjeta]:
    """Devuelve el número normalizado y el tipo, o lanza `TarjetaInvalida` con el campo."""
    numero = re.sub(r"[\s-]", "", str(datos.get("numero") or ""))
    if not luhn_valido(numero):
        raise TarjetaInvalida("numero")
    nombre = str(datos.get("nombre") or "").strip()
    if not 2 <= len(nombre) <= 100:
        raise TarjetaInvalida("nombre")
    if not _vencimiento_futuro(str(datos.get("vencimiento") or ""), hoy):
        raise TarjetaInvalida("vencimiento")
    if not re.fullmatch(r"\d{3,4}", str(datos.get("cvv") or "")):
        raise TarjetaInvalida("cvv")
    tipo = datos.get("tipo")
    if tipo not in ("CREDITO", "DEBITO"):
        raise TarjetaInvalida("tipo")
    return numero, tipo


def resultado_de(numero: str) -> tuple[Resultado, str | None]:
    return TARJETAS_PRUEBA.get(numero, ("aprobado", None))


def tokenizar(numero: str, tipo: TipoTarjeta) -> MedioTokenizado:
    return MedioTokenizado(
        token=f"tok_{secrets.token_urlsafe(18)}",
        marca=marca(numero),
        tipo=tipo,
        ultimos4=numero[-4:],
    )
