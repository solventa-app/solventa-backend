"""Adaptador de tasas de cambio (KAN-209): hoy, una tabla de referencia fija versionada
(`app/tasas_cambio.json`) que hace de stub del proveedor.

La web deja elegir la moneda en el header y el BFF convierte los montos con estas tasas
(ver `docs/evidencias/diseno-i18n-moneda-prima.md`). El BFF no habla con ningún proveedor:
las pide aquí (regla de arquitectura #2). Cambiar a un proveedor real (Banco de la República,
un API de FX) es otro adaptador que cumpla `PuertoTasasCambio`, con Circuit Breaker y caché
como Open Finance; los demás servicios no cambian.
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.domain.puertos import TablaTasas

ARCHIVO_TASAS = Path(__file__).resolve().parents[1] / "tasas_cambio.json"


class AdaptadorTasasReferencia:
    """Implementa `PuertoTasasCambio` leyendo la tabla una sola vez al arrancar."""

    def __init__(self, archivo: Path = ARCHIVO_TASAS) -> None:
        crudo = json.loads(archivo.read_text(encoding="utf-8"))
        tasas = {moneda: str(valor) for moneda, valor in crudo["cop_por_unidad"].items()}
        for moneda, valor in tasas.items():
            try:
                if Decimal(valor) <= 0:
                    raise ValueError(f"tasa no positiva para {moneda}")
            except InvalidOperation as error:
                raise ValueError(f"tasa inválida para {moneda}: {valor}") from error
        if tasas.get("COP") != "1":
            raise ValueError("la tabla debe tener COP = 1")
        self._tabla = TablaTasas(cop_por_unidad=tasas, fecha=crudo["fecha"], fuente=crudo["fuente"])

    def tasas(self) -> TablaTasas:
        return self._tabla
