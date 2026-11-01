"""Motor de decisión de preaprobación y estructuración de ofertas de crédito (HU-W02 / KAN-28).

Principios de diseño:
1. Función pura determinista: sin llamadas a red ni base de datos dentro del motor.
2. Reglas de decisión claras y transparentes:
   - APROBADO: Buen score (>= 650), bajo DTI (<= 35%), sin mora y fuentes completas.
   - CONDICIONADO: Score moderado (500–649), DTI elevado (35%–50%), mora menor (<= 60 días),
     contraoferta por capacidad o fuentes preliminares.
   - RECHAZADO: Score insuficiente (< 500), mora severa (> 60 días), DTI crítico (> 50%)
     o capacidad nula.
3. Capacidad de pago en moneda local respetando DTI y porción prudencial del flujo libre.
4. Cálculo de tasa de interés ajustada al riesgo (respetando tope legal de usura) y prima estimada.
5. Sugerencia de plazo para optimizar la cuota mensual ante montos solicitados altos.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.application.motor_scoring import (
    PerfilFinancieroEntrada,
    Razon,
    ResultadoMotorScore,
    calcular_score_motor,
)
from app.limites_plazo import limites_plazo

APROBADO = "APROBADO"
CONDICIONADO = "CONDICIONADO"
RECHAZADO = "RECHAZADO"
APROBADO_PARCIAL = "APROBADO_PARCIAL"
SIN_INFORMACION = "SIN_INFORMACION"
REVISION_MANUAL = "REVISION_MANUAL"

PORCION_FLUJO_LIBRE = Decimal("0.60")
UNO = Decimal(1)
DOCE = Decimal(12)


class PoliticaCredito(BaseModel):
    """Parámetros de política de riesgo y condiciones comerciales."""

    score_minimo: int = 500
    score_aprobado_directo: int = 650
    dti_maximo: Decimal = Decimal("0.40")  # 40% del ingreso para cuotas totales
    dti_limite_rechazo: Decimal = Decimal("0.50")  # > 50% rechazo directo
    monto_min: Decimal = Decimal("500000")
    monto_max: Decimal = Decimal("80000000")
    paso_monto: Decimal = Decimal("10000")
    # T18: los límites salen de jurisdicciones.json, no de números escritos aquí.
    plazo_min: int = Field(default_factory=lambda: limites_plazo()[0])
    plazo_max: int = Field(default_factory=lambda: limites_plazo()[1])
    tasa_referencia_ea: Decimal = Decimal("0.10")  # 10.0% EA base
    tasa_maxima_legal_ea: Decimal = Decimal("0.30")  # 30.0% EA tope legal (usura)
    moneda: str = "COP"
    tasa_base_mensual_prima: Decimal = Decimal("0.00045")


class ResultadoDecision(BaseModel):
    """Resultado formal de la evaluación de preaprobación."""

    estado: str  # APROBADO, CONDICIONADO, RECHAZADO, SIN_INFORMACION
    score: int | None = None
    clasificacion_riesgo: str | None = None
    monto_solicitado: Decimal
    monto_aprobado: Decimal | None = None
    monto_maximo_capacidad: Decimal | None = None
    plazo_meses: int
    plazo_sugerido: int | None = None
    tasa_ea: Decimal | None = None
    tasa_mensual: Decimal | None = None
    cuota_mensual: Decimal | None = None
    prima_estimada: Decimal | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    razones: list[Razon] = Field(default_factory=list)
    condiciones: list[str] = Field(default_factory=list)
    es_preliminar: bool = False


def redondear(monto: Decimal, moneda: str = "COP") -> Decimal:
    decimales = 0 if moneda in {"CLP", "JPY"} else 2
    exp = Decimal(1).scaleb(-decimales)
    return monto.quantize(exp, rounding=ROUND_HALF_UP)


def truncar_a_paso(monto: Decimal, paso: Decimal) -> Decimal:
    """Ajusta hacia abajo al múltiplo comercial más cercano."""
    if paso <= 0:
        return monto
    return (monto / paso).to_integral_value(rounding=ROUND_DOWN) * paso


def ea_a_mensual(tasa_ea: Decimal) -> Decimal:
    """Convierte tasa Efectiva Anual a tasa Efectiva Mensual equivalente."""
    return (UNO + tasa_ea) ** (UNO / DOCE) - UNO


def cuota_francesa(principal: Decimal, tasa_mensual: Decimal, n: int) -> Decimal:
    """Cuota fija periódica (sistema de amortización francés)."""
    if n <= 0:
        return Decimal(0)
    if tasa_mensual == 0:
        return principal / Decimal(n)
    denominador = UNO - (UNO + tasa_mensual) ** -n
    if denominador == 0:
        return principal / Decimal(n)
    return principal * tasa_mensual / denominador


def valor_presente(cuota: Decimal, tasa_mensual: Decimal, n: int) -> Decimal:
    """Calcula el monto máximo prestable a partir de una cuota máxima sostenible."""
    if n <= 0:
        return Decimal(0)
    if tasa_mensual == 0:
        return cuota * Decimal(n)
    return cuota * (UNO - (UNO + tasa_mensual) ** -n) / tasa_mensual


def spread_por_score(score: int) -> Decimal | None:
    """Margen adicional sobre la tasa de referencia según el score del cliente."""
    if score >= 800:
        return Decimal("0.05")
    if score >= 700:
        return Decimal("0.09")
    if score >= 600:
        return Decimal("0.14")
    if score >= 500:
        return Decimal("0.20")
    return None


def sugerir_plazo(
    monto_deseado: Decimal,
    cuota_maxima: Decimal,
    tasa_mensual: Decimal,
    plazo_actual: int,
    plazo_max: int,
) -> int:
    """Encuentra el plazo mínimo que soporte el monto deseado dentro de la cuota máxima."""
    for n in range(plazo_actual, plazo_max + 1, 6):
        capacidad = valor_presente(cuota_maxima, tasa_mensual, n)
        if capacidad >= monto_deseado:
            return n
    return plazo_max


def decidir_preaprobacion(
    perfil: PerfilFinancieroEntrada | None,
    monto_solicitado: Decimal,
    plazo_meses: int,
    politica: PoliticaCredito | None = None,
) -> ResultadoDecision:
    """Aplica las reglas de preaprobación y genera la decisión y oferta financiera."""
    politica = politica or PoliticaCredito()

    if perfil is None or not perfil.ingresos_mensuales:
        return ResultadoDecision(
            estado=SIN_INFORMACION,
            monto_solicitado=monto_solicitado,
            plazo_meses=plazo_meses,
            razones=[
                Razon(
                    codigo="SIN_DATOS_FINANCIEROS",
                    descripcion=(
                        "No hay información financiera suficiente disponible para preaprobación"
                    ),
                    factor="general",
                )
            ],
            condiciones=["Se requiere solicitud tradicional con documentación física de ingresos"],
        )

    r_score: ResultadoMotorScore = calcular_score_motor(perfil)
    variables = dict(r_score.variables)
    razones = list(r_score.razones)
    condiciones: list[str] = []

    # 1. Regla de Score Mínimo
    spread = spread_por_score(r_score.score)
    if r_score.score < politica.score_minimo or spread is None:
        razones.insert(
            0,
            Razon(
                codigo="SCORE_INSUFICIENTE",
                descripcion=(
                    f"Score ({r_score.score}) inferior al mínimo ({politica.score_minimo})"
                ),
                impacto=-100,
                factor="general",
            ),
        )
        return ResultadoDecision(
            estado=RECHAZADO,
            score=r_score.score,
            clasificacion_riesgo=r_score.clasificacion,
            monto_solicitado=monto_solicitado,
            plazo_meses=plazo_meses,
            variables=variables,
            razones=razones,
            es_preliminar=r_score.es_preliminar,
        )

    # 2. Regla de Mora Severa
    if perfil.dias_mora > 60:
        razones.insert(
            0,
            Razon(
                codigo="MORA_SEVERA_RECHAZO",
                descripcion=(
                    f"Mora reportada de {perfil.dias_mora} días impide el otorgamiento de crédito"
                ),
                impacto=-150,
                factor="bureau",
            ),
        )
        return ResultadoDecision(
            estado=RECHAZADO,
            score=r_score.score,
            clasificacion_riesgo=r_score.clasificacion,
            monto_solicitado=monto_solicitado,
            plazo_meses=plazo_meses,
            variables=variables,
            razones=razones,
            es_preliminar=r_score.es_preliminar,
        )

    # 3. Tasa de Interés por Riesgo y Tope Legal
    tasa_ea = (politica.tasa_referencia_ea + spread).quantize(Decimal("0.0001"))
    if tasa_ea > politica.tasa_maxima_legal_ea:
        razones.insert(
            0,
            Razon(
                codigo="TASA_EXCEDE_TOPE_LEGAL",
                descripcion=(
                    f"Tasa requerida ({tasa_ea:.2%}) supera el tope legal"
                    f" ({politica.tasa_maxima_legal_ea:.2%})"
                ),
                impacto=-100,
                factor="general",
            ),
        )
        return ResultadoDecision(
            estado=RECHAZADO,
            score=r_score.score,
            clasificacion_riesgo=r_score.clasificacion,
            monto_solicitado=monto_solicitado,
            plazo_meses=plazo_meses,
            variables=variables,
            razones=razones,
            es_preliminar=r_score.es_preliminar,
        )

    i = ea_a_mensual(tasa_ea)

    # 4. Capacidad de Pago y DTI
    ingreso = sum(perfil.ingresos_mensuales) / len(perfil.ingresos_mensuales)
    flujo_libre = ingreso - perfil.gastos_mensuales_promedio - perfil.cuotas_deuda_vigentes
    dti_actual = variables.get("dti_actual", 0.0)

    limite_dti_efectivo = politica.dti_limite_rechazo if dti_actual > 0.35 else politica.dti_maximo
    margen_dti = ingreso * limite_dti_efectivo - perfil.cuotas_deuda_vigentes
    margen_flujo = flujo_libre * PORCION_FLUJO_LIBRE

    cuota_max = min(margen_dti, margen_flujo)
    variables["cuota_maxima_sostenible"] = float(
        redondear(max(Decimal(0), cuota_max), politica.moneda)
    )

    if cuota_max <= 0 or dti_actual >= float(politica.dti_limite_rechazo):
        codigo = (
            "DTI_EXCESIVO"
            if dti_actual >= float(politica.dti_limite_rechazo)
            else "CAPACIDAD_PAGO_INSUFICIENTE"
        )
        razones.insert(
            0,
            Razon(
                codigo=codigo,
                descripcion="Capacidad de pago insuficiente o endeudamiento supera el límite",
                impacto=-150,
                factor="dti",
            ),
        )
        return ResultadoDecision(
            estado=RECHAZADO,
            score=r_score.score,
            clasificacion_riesgo=r_score.clasificacion,
            monto_solicitado=monto_solicitado,
            plazo_meses=plazo_meses,
            variables=variables,
            razones=razones,
            es_preliminar=r_score.es_preliminar,
        )

    # 5. Monto Máximo por Capacidad
    monto_capacidad = valor_presente(cuota_max, i, plazo_meses)
    monto_capacidad = min(monto_capacidad, politica.monto_max)
    monto_capacidad = truncar_a_paso(monto_capacidad, politica.paso_monto)
    monto_capacidad = redondear(monto_capacidad, politica.moneda)

    if monto_capacidad < politica.monto_min:
        razones.insert(
            0,
            Razon(
                codigo="MONTO_MAXIMO_BAJO_MINIMO",
                descripcion=(
                    f"Capacidad máxima ({monto_capacidad}) inferior al mínimo"
                    f" ({politica.monto_min})"
                ),
                impacto=-100,
                factor="dti",
            ),
        )
        return ResultadoDecision(
            estado=RECHAZADO,
            score=r_score.score,
            clasificacion_riesgo=r_score.clasificacion,
            monto_solicitado=monto_solicitado,
            monto_maximo_capacidad=monto_capacidad,
            plazo_meses=plazo_meses,
            variables=variables,
            razones=razones,
            es_preliminar=r_score.es_preliminar,
        )

    # 6. Evaluación de Estado: APROBADO vs CONDICIONADO
    es_buen_perfil = (
        r_score.score >= politica.score_aprobado_directo
        and dti_actual <= 0.35
        and perfil.dias_mora == 0
        and not r_score.es_preliminar
    )

    plazo_sugerido = plazo_meses

    if es_buen_perfil and monto_solicitado <= monto_capacidad:
        estado = APROBADO
        monto_final = monto_solicitado
        razones.insert(
            0,
            Razon(
                codigo="PREAPROBACION_APROBADA",
                descripcion="Cumple plenamente con políticas de scoring, bajo DTI y capacidad",
                impacto=50,
                factor="general",
            ),
        )
    else:
        estado = CONDICIONADO
        monto_final = min(monto_solicitado, monto_capacidad)

        if monto_solicitado > monto_capacidad:
            razones.insert(
                0,
                Razon(
                    codigo="CONTRAOFERTA_CAPACIDAD_PAGO",
                    descripcion=(
                        f"Monto ajustado de {monto_solicitado} a {monto_capacidad}"
                        f" {politica.moneda} por capacidad"
                    ),
                    impacto=-20,
                    factor="dti",
                ),
            )
            condiciones.append(
                f"Monto preaprobado limitado a capacidad máxima sostenible de {monto_capacidad}"
                f" {politica.moneda}"
            )
            if plazo_meses < politica.plazo_max:
                plazo_sugerido = sugerir_plazo(
                    monto_solicitado, cuota_max, i, plazo_meses, politica.plazo_max
                )
                if plazo_sugerido > plazo_meses:
                    condiciones.append(
                        f"Se sugiere extender el plazo a {plazo_sugerido} meses para mayor cupo"
                    )

        if perfil.dias_mora > 0:
            razones.insert(
                0,
                Razon(
                    codigo="MORA_MENOR_CONDICIONADO",
                    descripcion=(
                        f"Mora de {perfil.dias_mora} días; se requiere certificación al día"
                    ),
                    impacto=-40,
                    factor="bureau",
                ),
            )
            condiciones.append(
                "Presentar paz y salvo o certificación de pago al día de obligaciones reportadas"
            )

        if dti_actual > 0.35:
            razones.insert(
                0,
                Razon(
                    codigo="DTI_ALTO_CONDICIONADO",
                    descripcion=(
                        f"Endeudamiento actual ({dti_actual:.1%}) supera el 35%; requiere garantía"
                    ),
                    impacto=-30,
                    factor="dti",
                ),
            )
            condiciones.append(
                "Sujeto a consolidación de pasivos vigentes o codeudor con ingresos demostrables"
            )

        if r_score.score < politica.score_aprobado_directo:
            razones.insert(
                0,
                Razon(
                    codigo="SCORE_MODERADO_CONDICIONADO",
                    descripcion=(
                        f"Score moderado ({r_score.score}); requiere validación de ingresos"
                    ),
                    impacto=-25,
                    factor="general",
                ),
            )
            condiciones.append("Adjuntar certificados laborales o extractos bancarios recientes")

        if r_score.es_preliminar:
            razones.insert(
                0,
                Razon(
                    codigo="FUENTES_PARCIALES_PRELIMINAR",
                    descripcion="Oferta preliminar calculada con fuentes parciales o degradadas",
                    impacto=-15,
                    factor="general",
                ),
            )
            condiciones.append(
                "Oferta preliminar sujeta a ratificación una vez se consulte totalidad de fuentes"
            )

    monto_aprobado = redondear(monto_final, politica.moneda)
    cuota_mensual = redondear(cuota_francesa(monto_aprobado, i, plazo_meses), politica.moneda)

    factor_riesgo = Decimal("2.2") - (Decimal(r_score.score) / Decimal(1000)) * Decimal("1.4")
    prima_estimada = redondear(
        monto_aprobado * politica.tasa_base_mensual_prima * factor_riesgo,
        politica.moneda,
    )

    return ResultadoDecision(
        estado=estado,
        score=r_score.score,
        clasificacion_riesgo=r_score.clasificacion,
        monto_solicitado=monto_solicitado,
        monto_aprobado=monto_aprobado,
        monto_maximo_capacidad=monto_capacidad,
        plazo_meses=plazo_meses,
        plazo_sugerido=plazo_sugerido,
        tasa_ea=tasa_ea,
        tasa_mensual=i.quantize(Decimal("0.00000001")),
        cuota_mensual=cuota_mensual,
        prima_estimada=prima_estimada,
        variables={
            **variables,
            "tasa_ea": str(tasa_ea),
            "monto_capacidad_max": float(monto_capacidad),
        },
        razones=razones,
        condiciones=condiciones,
        es_preliminar=r_score.es_preliminar,
    )


def evaluar_fuentes_preaprobacion(
    fuentes: list[dict[str, Any]],
    monto_solicitado: Decimal,
    plazo_meses: int = 24,
    politica: PoliticaCredito | None = None,
) -> ResultadoDecision:
    """Evalúa directamente una lista de fuentes crudas de ACL Worker y genera la preaprobación."""
    from app.application.motor_scoring import extraer_perfil_desde_fuentes

    politica = politica or PoliticaCredito()
    perfil = extraer_perfil_desde_fuentes(fuentes, moneda=politica.moneda)
    return decidir_preaprobacion(perfil, monto_solicitado, plazo_meses, politica)
