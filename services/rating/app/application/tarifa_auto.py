"""Tarifa de la póliza de auto todo riesgo (KAN-290, `docs/plan-poliza-auto.md` §3).

Toda la tarifa vive en `contracts/tarifas-auto.json` (copia en `app/tarifas-auto.json`): cambiarla
es editar ese archivo, sin tocar el código (escenario 6.5, «cambio en reglas de rating»).

    prima_anual = valor_comercial × tasa_base(clase) × factores + prima_rce(clase)
    prima_mensual = prima_anual / 12

Los factores son el uso, la antigüedad, el kilometraje, el índice de hurto, la siniestralidad, la
ciudad y el deducible. Función pura sobre el resumen que guardó RISK: sin red ni base de datos.
"""

import json
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

DOS_DECIMALES = Decimal("0.01")
ESTADOS_NO_ASEGURABLES = {
    "CON_LIMITACION": "El vehículo tiene una limitación a la propiedad registrada en el RUNT.",
    "HURTADO": "El vehículo tiene una denuncia de hurto vigente en el RUNT.",
}


def _cargar() -> dict[str, Any]:
    actual = Path(__file__).resolve()
    for archivo in [actual.parents[1] / "tarifas-auto.json"] + [
        ancestro / "contracts" / "tarifas-auto.json" for ancestro in actual.parents
    ]:
        if archivo.is_file():
            return json.loads(archivo.read_text(encoding="utf-8"))
    raise FileNotFoundError("no se encontró tarifas-auto.json")


TARIFA = _cargar()


class VehiculoNoEncontrado(Exception):
    """RUNT no tiene la placa."""


class VehiculoNoAsegurable(Exception):
    """El vehículo existe pero no se puede asegurar en todo riesgo; el mensaje dice por qué."""


class DatosVehiculoInsuficientes(Exception):
    """RUNT no respondió: sin clase ni modelo no hay cómo estimar nada."""


@dataclass
class Cotizacion:
    tipo: str  # COMPLETA o PRELIMINAR
    prima_mensual: Decimal
    valor_comercial: Decimal
    valor_estimado: bool  # True si Fasecolda no respondió y el valor es de referencia
    requiere_revision: bool  # demasiados siniestros: la suscripción lo revisa (UNDER)
    factores: list[dict[str, Any]] = field(default_factory=list)
    coberturas: list[dict[str, Any]] = field(default_factory=list)


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto.strip().upper())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _por_tramo(tramos: list[dict[str, Any]], llave: str, valor: int) -> Decimal:
    for tramo in tramos:
        tope = tramo[llave]
        if tope is None or valor <= tope:
            return Decimal(tramo["factor"])
    return Decimal(tramos[-1]["factor"])


def _texto_deducible(deducible: str) -> str | None:
    if deducible == "SIN_DEDUCIBLE":
        return None
    porcentaje = int(Decimal(TARIFA["deducible"]["porcentaje"]) * 100)
    return f"{porcentaje} % mín. {TARIFA['deducible']['minimo_smmlv']} SMMLV"


def monto(valor: Decimal, moneda: str | None = None) -> dict[str, str]:
    return {"valor": str(valor.quantize(DOS_DECIMALES)), "moneda": moneda or TARIFA["moneda"]}


def cotizar(
    resumen: dict[str, Any],
    kilometraje_anual: int,
    ciudad: str,
    uso: str,
    deducible: str,
    anio_actual: int,
) -> Cotizacion:
    if not resumen.get("encontrado", True):
        raise VehiculoNoEncontrado()
    clase, modelo = resumen.get("clase"), resumen.get("modelo")
    if not clase or not modelo:
        raise DatosVehiculoInsuficientes()
    if resumen.get("estado") in ESTADOS_NO_ASEGURABLES:
        raise VehiculoNoAsegurable(ESTADOS_NO_ASEGURABLES[resumen["estado"]])
    if clase not in TARIFA["tasa_base_anual"]:
        raise VehiculoNoAsegurable("No aseguramos vehículos de esa clase.")
    antiguedad = max(0, anio_actual - int(modelo))
    if antiguedad > TARIFA["antiguedad_max_anios"]:
        raise VehiculoNoAsegurable(
            f"El vehículo tiene más de {TARIFA['antiguedad_max_anios']} años: no lo aseguramos "
            "en todo riesgo."
        )

    # Valor comercial: el de Fasecolda o, si no respondió, uno de referencia (oferta preliminar).
    valor_estimado = resumen.get("valor_comercial") is None
    if valor_estimado:
        referencia = TARIFA["valor_referencia_preliminar"]
        depreciacion = (1 - Decimal(referencia["depreciacion_anual"])) ** antiguedad
        valor = Decimal(referencia["valor_nuevo"][clase]) * depreciacion
    else:
        valor = Decimal(resumen["valor_comercial"])
    valor = valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # Uso: el declarado o, si el RUNT dice servicio público, comercial.
    uso_efectivo = "COMERCIAL" if resumen.get("servicio") == "PUBLICO" else uso
    # Kilometraje: el declarado, salvo que el odómetro muestre un promedio anual mayor.
    odometro = resumen.get("odometro_km")
    km_odometro = int(odometro) // max(1, antiguedad) if odometro else 0
    km = max(kilometraje_anual, km_odometro)
    siniestros = resumen.get("siniestros_3_anios")
    indice = resumen.get("indice_hurto") or "MEDIO"
    ciudad_n = _normalizar(ciudad)
    factor_ciudad = TARIFA["factor_ciudad"].get(ciudad_n, TARIFA["factor_ciudad"]["_OTRAS"])

    factores = [
        ("USO", Decimal(TARIFA["factor_uso"][uso_efectivo]), uso_efectivo.lower()),
        (
            "ANTIGUEDAD",
            _por_tramo(TARIFA["factor_antiguedad"], "hasta_anios", antiguedad),
            f"{antiguedad} años",
        ),
        (
            "KILOMETRAJE",
            _por_tramo(TARIFA["factor_kilometraje"], "hasta_km", km),
            f"{km:,} km/año".replace(",", "."),
        ),
        ("INDICE_HURTO", Decimal(TARIFA["factor_indice_hurto"][indice]), f"índice {indice}"),
        (
            "SINIESTRALIDAD",
            Decimal("1.00")
            if siniestros is None
            else _por_tramo(TARIFA["factor_siniestralidad"], "siniestros", int(siniestros)),
            "sin dato" if siniestros is None else f"{siniestros} en 3 años",
        ),
        ("CIUDAD", Decimal(factor_ciudad), ciudad.strip()),
        ("DEDUCIBLE", Decimal(TARIFA["factor_deducible"][deducible]), deducible),
    ]
    producto = Decimal(1)
    for _nombre, factor, _detalle in factores:
        producto *= factor
    anual = valor * Decimal(TARIFA["tasa_base_anual"][clase]) * producto + Decimal(
        TARIFA["prima_rce_anual"][clase]
    )
    mensual = (anual / 12).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)

    texto_deducible = _texto_deducible(deducible)
    coberturas = [
        {
            "tipo": "RESPONSABILIDAD_CIVIL",
            "valorAsegurado": monto(Decimal(TARIFA["valor_asegurado_rce"])),
            "deducible": None,
        },
        *[
            {"tipo": tipo, "valorAsegurado": monto(valor), "deducible": texto_deducible}
            for tipo in (
                "PERDIDA_TOTAL_DANOS",
                "PERDIDA_PARCIAL_DANOS",
                "PERDIDA_TOTAL_HURTO",
                "PERDIDA_PARCIAL_HURTO",
            )
        ],
        {"tipo": "ASISTENCIA", "valorAsegurado": None, "deducible": None},
    ]
    return Cotizacion(
        tipo="PRELIMINAR" if valor_estimado or siniestros is None else "COMPLETA",
        prima_mensual=mensual,
        valor_comercial=valor,
        valor_estimado=valor_estimado,
        requiere_revision=siniestros is not None
        and int(siniestros) >= TARIFA["siniestros_para_revision"],
        factores=[
            {"nombre": nombre, "factor": float(factor), "detalle": detalle}
            for nombre, factor, detalle in factores
        ],
        coberturas=coberturas,
    )


def rango_preliminar(prima: Decimal) -> tuple[Decimal, Decimal]:
    margen = Decimal(TARIFA["rango_preliminar"])
    return (
        (prima * (1 - margen)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP),
        (prima * (1 + margen)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP),
    )
