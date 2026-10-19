"""Motor de scoring de riesgo crediticio (HU-W02 / KAN-28).

Principios de diseño:
1. Score interno en escala 0–1000 basado en reglas explicables y transparentes.
2. Desglose detallado por cuatro factores clave:
   - bureau: historial crediticio en centrales y moras.
   - open_finance: colchón de liquidez, sobregiros y antigüedad de cuentas.
   - ingresos: estabilidad (coeficiente de variación) y ratio de gasto.
   - dti: ratio de apalancamiento y endeudamiento actual.
3. Clasificación de riesgo objetiva: BAJO, MEDIO, ALTO.
4. Soporte para extracción automática desde fuentes de ACL y manejo de fuentes parciales/degradadas.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

SCORE_BASE = 500


class Razon(BaseModel):
    codigo: str
    descripcion: str
    impacto: int = 0
    factor: str = "general"


class FactorDesglose(BaseModel):
    factor: str
    nombre: str
    puntos: int = 0
    impacto_neto: int = 0
    razones: list[Razon] = Field(default_factory=list)


class PerfilFinancieroEntrada(BaseModel):
    """Datos agregados del cliente para el cálculo del score de riesgo."""

    moneda: str = "COP"
    ingresos_mensuales: list[Decimal] = Field(default_factory=list)
    gastos_mensuales_promedio: Decimal = Decimal(0)
    cuotas_deuda_vigentes: Decimal = Decimal(0)
    saldo_promedio_90d: Decimal = Decimal(0)
    score_bureau: int | None = None
    dias_mora: int = 0
    sobregiros_6m: int = 0
    antiguedad_cuentas_meses: int = 0
    numero_cuentas: int = 1
    productos_activos: int = 1
    fuentes_degradadas: list[str] = Field(default_factory=list)
    es_preliminar: bool = False


class ResultadoMotorScore(BaseModel):
    """Resultado del cálculo del score con desglose y explicabilidad."""

    score: int
    score_normalizado: float
    clasificacion: str  # "BAJO", "MEDIO", "ALTO"
    nivel_riesgo: str  # "A", "B", "C", "D", "E"
    variables: dict[str, float]
    razones: list[Razon]
    desglose_por_factor: dict[str, FactorDesglose]
    es_preliminar: bool = False


def calcular_variables(p: PerfilFinancieroEntrada) -> dict[str, float]:
    """Calcula ratios y variables financieras normalizadas."""
    if p.ingresos_mensuales:
        ingresos_float = [float(x) for x in p.ingresos_mensuales]
        ingreso = statistics.fmean(ingresos_float)
        cv = (
            round(statistics.pstdev(ingresos_float) / ingreso, 4)
            if len(ingresos_float) > 1 and ingreso > 0
            else 0.0
        )
        gastos = float(p.gastos_mensuales_promedio)
        cuotas = float(p.cuotas_deuda_vigentes)
        saldo = float(p.saldo_promedio_90d)
        dti = round(cuotas / ingreso, 4) if ingreso > 0 else 1.0
        ratio_gasto = round(gastos / ingreso, 4) if ingreso > 0 else 1.0
        colchon = round(saldo / ingreso, 4) if ingreso > 0 else 0.0
        flujo = round(ingreso - gastos - cuotas, 2)
    else:
        ingreso = 0.0
        cv = 0.0
        gastos = float(p.gastos_mensuales_promedio)
        cuotas = float(p.cuotas_deuda_vigentes)
        saldo = float(p.saldo_promedio_90d)
        dti = 1.0  # Sin ingresos declarados el apalancamiento es total
        ratio_gasto = 1.0
        colchon = 0.0
        flujo = round(0.0 - gastos - cuotas, 2)

    return {
        "ingreso_promedio": round(ingreso, 2),
        "cv_ingresos": cv,
        "dti_actual": dti,
        "ratio_gasto": ratio_gasto,
        "flujo_libre": flujo,
        "colchon_liquidez": colchon,
        "sobregiros_6m": float(p.sobregiros_6m),
        "antiguedad_meses": float(p.antiguedad_cuentas_meses),
        "score_bureau": float(p.score_bureau) if p.score_bureau is not None else -1.0,
        "dias_mora": float(p.dias_mora),
    }


def clasificar_riesgo(score: int) -> tuple[str, str]:
    """Determina la clasificación macro ("BAJO", "MEDIO", "ALTO") y el nivel de riesgo ("A"-"E")."""
    if score >= 750:
        return "BAJO", "A"
    if score >= 650:
        return "MEDIO", "B"
    if score >= 550:
        return "MEDIO", "C"
    if score >= 450:
        return "ALTO", "D"
    return "ALTO", "E"


def calcular_score_motor(perfil: PerfilFinancieroEntrada) -> ResultadoMotorScore:
    """Evalúa el perfil financiero y calcula el score de riesgo (0-1000) con desglose."""
    variables = calcular_variables(perfil)
    razones: list[Razon] = []
    desglose: dict[str, FactorDesglose] = {
        "dti": FactorDesglose(factor="dti", nombre="Endeudamiento y DTI"),
        "ingresos": FactorDesglose(factor="ingresos", nombre="Estabilidad de Ingresos"),
        "open_finance": FactorDesglose(factor="open_finance", nombre="Comportamiento Bancario"),
        "bureau": FactorDesglose(factor="bureau", nombre="Historial en Centrales de Riesgo"),
    }

    # 1. Factor DTI
    dti = variables["dti_actual"]
    if dti < 0.10:
        r = Razon(
            codigo="DTI_BAJO",
            descripcion="Endeudamiento actual bajo (< 10%)",
            impacto=120,
            factor="dti",
        )
    elif dti < 0.20:
        r = Razon(
            codigo="DTI_MODERADO",
            descripcion="Endeudamiento actual moderado (10% - 20%)",
            impacto=60,
            factor="dti",
        )
    elif dti < 0.35:
        r = Razon(
            codigo="DTI_MEDIO",
            descripcion="Endeudamiento actual medio (20% - 35%)",
            impacto=0,
            factor="dti",
        )
    elif dti < 0.50:
        r = Razon(
            codigo="DTI_ELEVADO",
            descripcion="Endeudamiento actual elevado (35% - 50%)",
            impacto=-80,
            factor="dti",
        )
    else:
        r = Razon(
            codigo="DTI_ALTO",
            descripcion="Endeudamiento crítico (> 50%)",
            impacto=-150,
            factor="dti",
        )
    razones.append(r)
    desglose["dti"].impacto_neto += r.impacto
    desglose["dti"].razones.append(r)

    # 2. Factor Ingresos (Estabilidad y Rango de Gastos)
    cv = variables["cv_ingresos"]
    if cv < 0.08:
        r_cv = Razon(
            codigo="INGRESO_ESTABLE",
            descripcion="Ingresos mensuales altamente estables",
            impacto=70,
            factor="ingresos",
        )
    elif cv < 0.15:
        r_cv = Razon(
            codigo="INGRESO_REGULAR",
            descripcion="Ingresos mensuales regulares",
            impacto=35,
            factor="ingresos",
        )
    elif cv < 0.25:
        r_cv = Razon(
            codigo="INGRESO_VARIABLE",
            descripcion="Ingresos mensuales con variación",
            impacto=0,
            factor="ingresos",
        )
    else:
        r_cv = Razon(
            codigo="INGRESO_VOLATIL",
            descripcion="Ingresos mensuales volátiles",
            impacto=-70,
            factor="ingresos",
        )
    razones.append(r_cv)
    desglose["ingresos"].impacto_neto += r_cv.impacto
    desglose["ingresos"].razones.append(r_cv)

    ratio_gasto = variables["ratio_gasto"]
    if ratio_gasto <= 0.60:
        r_gasto = Razon(
            codigo="GASTO_CONTROLADO",
            descripcion="Gastos de vida bajos respecto a ingresos (<= 60%)",
            impacto=30,
            factor="ingresos",
        )
    elif ratio_gasto <= 0.80:
        r_gasto = Razon(
            codigo="GASTO_MODERADO",
            descripcion="Gastos de vida en rango normal (60% - 80%)",
            impacto=0,
            factor="ingresos",
        )
    elif ratio_gasto <= 0.90:
        r_gasto = Razon(
            codigo="GASTO_ALTO",
            descripcion="Gastos de vida elevados (80% - 90%)",
            impacto=-40,
            factor="ingresos",
        )
    else:
        r_gasto = Razon(
            codigo="GASTO_EXCESIVO",
            descripcion="Gastos de vida excesivos (> 90%)",
            impacto=-90,
            factor="ingresos",
        )
    razones.append(r_gasto)
    desglose["ingresos"].impacto_neto += r_gasto.impacto
    desglose["ingresos"].razones.append(r_gasto)

    # 3. Factor Open Finance (Liquidez, Sobregiros y Antigüedad)
    colchon = variables["colchon_liquidez"]
    if colchon >= 1.0:
        r_liq = Razon(
            codigo="LIQUIDEZ_ALTA",
            descripcion="Colchón de liquidez de al menos un mes de ingreso",
            impacto=60,
            factor="open_finance",
        )
    elif colchon >= 0.3:
        r_liq = Razon(
            codigo="LIQUIDEZ_MEDIA",
            descripcion="Colchón de liquidez adecuado",
            impacto=30,
            factor="open_finance",
        )
    elif colchon >= 0.1:
        r_liq = Razon(
            codigo="LIQUIDEZ_BAJA",
            descripcion="Colchón de liquidez bajo",
            impacto=0,
            factor="open_finance",
        )
    else:
        r_liq = Razon(
            codigo="SIN_LIQUIDEZ",
            descripcion="Sin colchón de liquidez apreciable",
            impacto=-40,
            factor="open_finance",
        )
    razones.append(r_liq)
    desglose["open_finance"].impacto_neto += r_liq.impacto
    desglose["open_finance"].razones.append(r_liq)

    sobregiros = int(variables["sobregiros_6m"])
    if sobregiros == 0:
        r_sob = Razon(
            codigo="SIN_SOBREGIROS",
            descripcion="Sin sobregiros bancarios en 6 meses",
            impacto=50,
            factor="open_finance",
        )
    elif sobregiros <= 2:
        r_sob = Razon(
            codigo="SOBREGIROS_OCASIONALES",
            descripcion="Sobregiros ocasionales (hasta 2)",
            impacto=0,
            factor="open_finance",
        )
    else:
        r_sob = Razon(
            codigo="SOBREGIROS_FRECUENTES",
            descripcion="Sobregiros frecuentes en cuentas",
            impacto=-80,
            factor="open_finance",
        )
    razones.append(r_sob)
    desglose["open_finance"].impacto_neto += r_sob.impacto
    desglose["open_finance"].razones.append(r_sob)

    antiguedad = int(variables["antiguedad_meses"])
    if antiguedad >= 36:
        r_ant = Razon(
            codigo="ANTIGUEDAD_ALTA",
            descripcion="Más de 3 años en el sistema financiero",
            impacto=40,
            factor="open_finance",
        )
    elif antiguedad >= 12:
        r_ant = Razon(
            codigo="ANTIGUEDAD_MEDIA",
            descripcion="Entre 1 y 3 años en el sistema financiero",
            impacto=15,
            factor="open_finance",
        )
    else:
        r_ant = Razon(
            codigo="ANTIGUEDAD_BAJA",
            descripcion="Menos de 1 año en el sistema financiero",
            impacto=-25,
            factor="open_finance",
        )
    razones.append(r_ant)
    desglose["open_finance"].impacto_neto += r_ant.impacto
    desglose["open_finance"].razones.append(r_ant)

    # 4. Factor Bureau (Score crediticio y moras)
    if perfil.score_bureau is None:
        r_bur = Razon(
            codigo="BUREAU_NO_DISPONIBLE",
            descripcion="Sin historial crediticio disponible en buró",
            impacto=0,
            factor="bureau",
        )
    elif perfil.score_bureau >= 750:
        r_bur = Razon(
            codigo="BUREAU_EXCELENTE",
            descripcion="Puntaje de crédito excelente en centrales",
            impacto=80,
            factor="bureau",
        )
    elif perfil.score_bureau >= 650:
        r_bur = Razon(
            codigo="BUREAU_BUENO",
            descripcion="Buen puntaje de crédito en centrales",
            impacto=40,
            factor="bureau",
        )
    elif perfil.score_bureau >= 550:
        r_bur = Razon(
            codigo="BUREAU_ACEPTABLE",
            descripcion="Puntaje crediticio aceptable",
            impacto=0,
            factor="bureau",
        )
    else:
        r_bur = Razon(
            codigo="BUREAU_DEFICIENTE",
            descripcion="Bajo puntaje de crédito en centrales",
            impacto=-70,
            factor="bureau",
        )
    razones.append(r_bur)
    desglose["bureau"].impacto_neto += r_bur.impacto
    desglose["bureau"].razones.append(r_bur)

    dias_mora = int(variables["dias_mora"])
    if dias_mora == 0:
        r_mora = Razon(
            codigo="SIN_MORA",
            descripcion="Al día en todas sus obligaciones",
            impacto=30,
            factor="bureau",
        )
    elif dias_mora <= 30:
        r_mora = Razon(
            codigo="MORA_MENOR",
            descripcion="Mora reciente menor a 30 días",
            impacto=-50,
            factor="bureau",
        )
    elif dias_mora <= 60:
        r_mora = Razon(
            codigo="MORA_MEDIA",
            descripcion="Mora entre 31 y 60 días",
            impacto=-100,
            factor="bureau",
        )
    else:
        r_mora = Razon(
            codigo="MORA_SEVERA",
            descripcion="Mora severa mayor a 60 días",
            impacto=-180,
            factor="bureau",
        )
    razones.append(r_mora)
    desglose["bureau"].impacto_neto += r_mora.impacto
    desglose["bureau"].razones.append(r_mora)

    # Impacto total acumulado
    total_impacto = sum(r.impacto for r in razones)
    score_final = max(0, min(1000, SCORE_BASE + total_impacto))
    score_norm = round(score_final / 10.0, 2)
    clasificacion, nivel_riesgo = clasificar_riesgo(score_final)

    # Asigna puntos base proporcionales en el desglose para visualización
    peso_base = SCORE_BASE // 4
    for fd in desglose.values():
        fd.puntos = max(0, peso_base + fd.impacto_neto)

    # Ordenar razones: más negativas primero para facilitar explicabilidad al cliente
    razones.sort(key=lambda x: x.impacto)

    return ResultadoMotorScore(
        score=score_final,
        score_normalizado=score_norm,
        clasificacion=clasificacion,
        nivel_riesgo=nivel_riesgo,
        variables=variables,
        razones=razones,
        desglose_por_factor=desglose,
        es_preliminar=perfil.es_preliminar or bool(perfil.fuentes_degradadas),
    )


def extraer_perfil_desde_fuentes(
    fuentes: list[dict[str, Any]],
    moneda: str = "COP",
) -> PerfilFinancieroEntrada:
    """Construye un `PerfilFinancieroEntrada` a partir de las fuentes recibidas de ACL Worker."""
    fuentes_map = {f.get("fuente"): f for f in fuentes if isinstance(f, dict) and "fuente" in f}
    fuentes_degradadas: list[str] = []

    for nombre, f in fuentes_map.items():
        if f.get("degradado") or f.get("de_cache") or not f.get("datos"):
            fuentes_degradadas.append(nombre)

    # 1. Buró / Historial Crediticio
    f_hist = fuentes_map.get("historial-crediticio", {})
    datos_hist = f_hist.get("datos") or {}
    score_bureau = datos_hist.get("score_crediticio")
    if score_bureau is not None:
        score_bureau = int(score_bureau)

    dias_mora = int(
        datos_hist.get("dias_mora")
        or datos_hist.get("mora_maxima_dias")
        or (30 if datos_hist.get("moras_vigentes") else 0)
    )

    cuotas_deuda = Decimal(str(datos_hist.get("cuotas_deuda_vigentes") or 0))

    # 2. Cuentas Bancarias / Open Finance
    f_cuentas = fuentes_map.get("cuentas-bancarias", {})
    datos_cuentas = f_cuentas.get("datos") or {}
    saldo_promedio = Decimal(
        str(datos_cuentas.get("saldo_promedio_90d") or datos_cuentas.get("saldo_promedio_3m") or 0)
    )
    sobregiros = int(datos_cuentas.get("sobregiros_6m") or 0)
    antiguedad_cuentas = int(
        datos_cuentas.get("antiguedad_cuentas_meses") or datos_cuentas.get("antiguedad_meses") or 24
    )
    productos = int(
        datos_cuentas.get("productos_activos") or datos_cuentas.get("numero_cuentas") or 1
    )
    gastos = Decimal(str(datos_cuentas.get("gastos_mensuales_promedio") or 0))

    # Si vienen cuotas en cuentas y no en historial, tomarlas
    if cuotas_deuda == 0 and "cuotas_deuda_vigentes" in datos_cuentas:
        cuotas_deuda = Decimal(str(datos_cuentas["cuotas_deuda_vigentes"]))

    # 3. Ingresos
    ingresos: list[Decimal] = []
    if "ingresos_mensuales" in datos_cuentas and isinstance(
        datos_cuentas["ingresos_mensuales"], list
    ):
        ingresos = [Decimal(str(x)) for x in datos_cuentas["ingresos_mensuales"]]

    # Fallback de ingresos con PILA o estimación razonable
    f_pila = fuentes_map.get("afiliacion-pila", {})
    datos_pila = f_pila.get("datos") or {}
    if not ingresos and "ingreso_base_cotizacion" in datos_pila:
        ibc = Decimal(str(datos_pila["ingreso_base_cotizacion"]))
        ingresos = [ibc] * 6
    elif not ingresos and "salario" in datos_pila:
        sal = Decimal(str(datos_pila["salario"]))
        ingresos = [sal] * 6
    elif not ingresos and saldo_promedio > 0:
        # Estimación conservadora basada en saldo promedio
        ingresos = [Decimal(str(round(float(saldo_promedio) * 0.75, 2)))] * 6
    elif not ingresos:
        # Thin file o datos incompletos
        ingresos = [Decimal("3000000")] * 6
        fuentes_degradadas.append("ingresos-estimados")

    if antiguedad_cuentas == 24 and "antiguedad_meses" in datos_pila:
        antiguedad_cuentas = int(datos_pila["antiguedad_meses"])

    # Gastos de vida estimados si no vinieron explícitos
    if gastos == 0 and ingresos:
        ingreso_prom = sum(ingresos) / len(ingresos)
        gastos = (ingreso_prom * Decimal("0.50")).quantize(Decimal("0.01"))

    # Si las cuotas son 0 pero registra obligaciones vigentes en bureau, estimar cuota típica
    if cuotas_deuda == 0 and datos_hist.get("obligaciones_vigentes"):
        ingreso_prom = sum(ingresos) / len(ingresos)
        cuotas_deuda = (ingreso_prom * Decimal("0.15")).quantize(Decimal("0.01"))

    es_preliminar = bool(fuentes_degradadas) or len(fuentes_map) < 2

    return PerfilFinancieroEntrada(
        moneda=moneda,
        ingresos_mensuales=ingresos,
        gastos_mensuales_promedio=gastos,
        cuotas_deuda_vigentes=cuotas_deuda,
        saldo_promedio_90d=saldo_promedio,
        score_bureau=score_bureau,
        dias_mora=dias_mora,
        sobregiros_6m=sobregiros,
        antiguedad_cuentas_meses=antiguedad_cuentas,
        numero_cuentas=max(1, productos),
        productos_activos=productos,
        fuentes_degradadas=fuentes_degradadas,
        es_preliminar=es_preliminar,
    )
