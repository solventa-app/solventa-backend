"""Comisiones de los socios de distribución (HU-W07, W07-7).

La comisión se causa **al emitir** la póliza (el primer pago es síncrono, así que emitir ya
implica cobrado): comisión = prima × tasa, con la tasa congelada al aceptar la oferta. Una por
póliza. El pago al socio queda fuera de alcance.

Funciones puras: el repositorio las usa dentro de la transacción de la emisión y para resumir las
ventas del socio por mes."""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _decimales(valor: str) -> int:
    return len(valor.split(".", 1)[1]) if "." in valor else 0


def calcular_comision(prima: str, tasa: Decimal) -> str:
    """La comisión como texto decimal, con los mismos decimales de la prima (la moneda manda)."""
    exponente = Decimal(1).scaleb(-_decimales(prima))
    return format((Decimal(prima) * tasa).quantize(exponente, rounding=ROUND_HALF_UP), "f")


def mes(momento: datetime | date) -> str:
    return f"{momento.year:04d}-{momento.month:02d}"


def resumir_ventas(
    propuestas_por_mes: dict[str, int],
    comisiones: Iterable[dict[str, Any]],
    moneda_por_defecto: str = "COP",
) -> dict[str, Any]:
    """Junta las propuestas aceptadas por mes con las comisiones (una por póliza emitida, con su
    `causada_en`, `moneda`, `prima` y `comision`). Los montos se suman como `Decimal`."""
    meses: dict[str, dict[str, Any]] = {}

    def fila(clave: str) -> dict[str, Any]:
        return meses.setdefault(
            clave, {"propuestas": 0, "emitidas": 0, "primas": Decimal(0), "comision": Decimal(0)}
        )

    moneda = moneda_por_defecto
    for clave, total in propuestas_por_mes.items():
        fila(clave)["propuestas"] = total
    for comision in comisiones:
        actual = fila(mes(comision["causada_en"]))
        actual["emitidas"] += 1
        actual["primas"] += Decimal(comision["prima"])
        actual["comision"] += Decimal(comision["comision"])
        moneda = comision["moneda"]

    def monto(valor: Decimal) -> dict[str, str]:
        return {"valor": format(valor, "f"), "moneda": moneda}

    resultado = [
        {
            "mes": clave,
            "propuestas": datos["propuestas"],
            "emitidas": datos["emitidas"],
            "primas": monto(datos["primas"]),
            "comision": monto(datos["comision"]),
        }
        for clave, datos in sorted(meses.items())
    ]
    totales = {
        "propuestas": sum(m["propuestas"] for m in meses.values()),
        "emitidas": sum(m["emitidas"] for m in meses.values()),
        "primas": monto(sum((m["primas"] for m in meses.values()), Decimal(0))),
        "comision": monto(sum((m["comision"] for m in meses.values()), Decimal(0))),
    }
    return {"meses": resultado, "totales": totales}
