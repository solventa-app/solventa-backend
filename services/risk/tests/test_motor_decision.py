"""Pruebas unitarias completas del motor de decisión de preaprobación (HU-W02 / KAN-28)."""

from decimal import Decimal

from app.application.motor_decision import (
    APROBADO,
    CONDICIONADO,
    RECHAZADO,
    SIN_INFORMACION,
    PoliticaCredito,
    cuota_francesa,
    decidir_preaprobacion,
    ea_a_mensual,
    evaluar_fuentes_preaprobacion,
    valor_presente,
)
from app.application.motor_scoring import PerfilFinancieroEntrada

D = Decimal


def _perfil_sano(
    ingreso: int = 7000000,
    gastos: int = 2500000,
    cuotas: int = 400000,  # DTI ~ 5.7%
    saldo: int = 10000000,
    score_bureau: int = 780,
    dias_mora: int = 0,
    sobregiros: int = 0,
    antiguedad: int = 48,
    es_preliminar: bool = False,
) -> PerfilFinancieroEntrada:
    return PerfilFinancieroEntrada(
        moneda="COP",
        ingresos_mensuales=[D(ingreso)] * 6,
        gastos_mensuales_promedio=D(gastos),
        cuotas_deuda_vigentes=D(cuotas),
        saldo_promedio_90d=D(saldo),
        score_bureau=score_bureau,
        dias_mora=dias_mora,
        sobregiros_6m=sobregiros,
        antiguedad_cuentas_meses=antiguedad,
        numero_cuentas=3,
        productos_activos=3,
        es_preliminar=es_preliminar,
    )


# ---------- Invariante matemática ----------


def test_cuota_francesa_y_valor_presente_son_inversos() -> None:
    i = ea_a_mensual(D("0.18"))
    monto_original = D("25000000")
    plazo = 36
    cuota = cuota_francesa(monto_original, i, plazo)
    vp = valor_presente(cuota, i, plazo)
    assert abs(vp - monto_original) < D("0.01")


# ---------- Cliente con buen score y bajo DTI -> APROBADO ----------


def test_cliente_buen_score_bajo_dti_aprobado_con_monto_solicitado() -> None:
    perfil = _perfil_sano()
    monto_solicitado = D("15000000")
    plazo = 24

    d = decidir_preaprobacion(perfil, monto_solicitado, plazo)

    assert d.estado == APROBADO
    assert d.score is not None and d.score >= 800
    assert d.clasificacion_riesgo == "BAJO"
    assert d.monto_aprobado == monto_solicitado
    assert d.monto_maximo_capacidad is not None and d.monto_maximo_capacidad >= monto_solicitado
    assert d.tasa_ea is not None and d.tasa_ea <= D("0.20")
    assert d.cuota_mensual is not None and d.cuota_mensual > 0
    assert d.prima_estimada is not None and d.prima_estimada > 0
    assert not d.es_preliminar

    codigos = [r.codigo for r in d.razones]
    assert "PREAPROBACION_APROBADA" in codigos


# ---------- Cliente con mora o alto DTI -> RECHAZADO o CONDICIONADO ----------


def test_cliente_con_mora_severa_es_rechazado_con_motivo_explicable() -> None:
    # Cliente con mora > 60 días
    perfil = _perfil_sano(score_bureau=540, dias_mora=75)
    monto_solicitado = D("10000000")
    plazo = 24

    d = decidir_preaprobacion(perfil, monto_solicitado, plazo)

    assert d.estado == RECHAZADO
    assert d.monto_aprobado is None
    codigos = [r.codigo for r in d.razones]
    assert "MORA_SEVERA_RECHAZO" in codigos


def test_cliente_con_alto_dti_critico_es_rechazado_por_capacidad() -> None:
    # DTI > 50%
    perfil = _perfil_sano(ingreso=4000000, gastos=2000000, cuotas=2400000)  # DTI = 0.60
    monto_solicitado = D("5000000")
    plazo = 24

    d = decidir_preaprobacion(perfil, monto_solicitado, plazo)

    assert d.estado == RECHAZADO
    codigos = [r.codigo for r in d.razones]
    assert any(c in {"DTI_EXCESIVO", "CAPACIDAD_PAGO_INSUFICIENTE"} for c in codigos)


def test_cliente_con_mora_menor_es_condicionado_con_condiciones_claras() -> None:
    # Mora de 15 días (menor a 60 días)
    perfil = _perfil_sano(score_bureau=680, dias_mora=15)
    monto_solicitado = D("10000000")
    plazo = 24

    d = decidir_preaprobacion(perfil, monto_solicitado, plazo)

    assert d.estado == CONDICIONADO
    assert d.monto_aprobado is not None
    codigos = [r.codigo for r in d.razones]
    assert "MORA_MENOR_CONDICIONADO" in codigos
    assert any("pago al día" in c.lower() or "paz y salvo" in c.lower() for c in d.condiciones)


def test_cliente_con_dti_elevado_moderado_es_condicionado() -> None:
    # DTI del 40% (entre 35% y 50%)
    perfil = _perfil_sano(ingreso=5000000, gastos=2000000, cuotas=2000000)
    monto_solicitado = D("8000000")
    plazo = 36

    d = decidir_preaprobacion(perfil, monto_solicitado, plazo)

    assert d.estado == CONDICIONADO
    codigos = [r.codigo for r in d.razones]
    assert "DTI_ALTO_CONDICIONADO" in codigos
    assert any("codeudor" in c.lower() or "consolidación" in c.lower() for c in d.condiciones)


def test_cliente_pide_mas_de_su_capacidad_recibe_contraoferta_y_plazo_sugerido() -> None:
    # Capacidad no alcanza para 60 millones a 12 meses
    perfil = _perfil_sano(ingreso=4000000, gastos=2000000, cuotas=500000)
    monto_solicitado = D("60000000")
    plazo_corto = 12

    d = decidir_preaprobacion(perfil, monto_solicitado, plazo_corto)

    assert d.estado == CONDICIONADO
    assert d.monto_aprobado is not None and d.monto_aprobado < monto_solicitado
    assert d.plazo_sugerido is not None and d.plazo_sugerido > plazo_corto

    codigos = [r.codigo for r in d.razones]
    assert "CONTRAOFERTA_CAPACIDAD_PAGO" in codigos


# ---------- Fuentes parciales o incompletas -> Oferta preliminar ----------


def test_fuentes_parciales_o_incompletas_generan_oferta_preliminar_condicionada() -> None:
    perfil = _perfil_sano(es_preliminar=True)
    perfil.fuentes_degradadas = ["cuentas-bancarias"]

    d = decidir_preaprobacion(perfil, D("10000000"), 24)

    assert d.estado == CONDICIONADO
    assert d.es_preliminar is True
    codigos = [r.codigo for r in d.razones]
    assert "FUENTES_PARCIALES_PRELIMINAR" in codigos
    assert any("preliminar" in c.lower() for c in d.condiciones)


# ---------- Casos de borde de política ----------


def test_sin_perfil_financiero_devuelve_sin_informacion() -> None:
    d = decidir_preaprobacion(None, D("5000000"), 24)
    assert d.estado == SIN_INFORMACION
    assert d.monto_aprobado is None


def test_tope_legal_de_tasa_bloquea_y_rechaza() -> None:
    # Perfil con score bajo que genera tasa por encima del tope legal simulado
    perfil = _perfil_sano(score_bureau=510, cuotas=1500000, dias_mora=20)
    politica_estricta = PoliticaCredito(
        tasa_referencia_ea=D("0.15"),
        tasa_maxima_legal_ea=D("0.25"),  # Con spread de 0.20 daría 0.35 > 0.25
    )

    d = decidir_preaprobacion(perfil, D("5000000"), 24, politica=politica_estricta)

    assert d.estado == RECHAZADO
    codigos = [r.codigo for r in d.razones]
    assert "TASA_EXCEDE_TOPE_LEGAL" in codigos


def test_evaluar_fuentes_preaprobacion_directo_con_fuentes_acl() -> None:
    fuentes_acl = [
        {
            "fuente": "historial-crediticio",
            "datos": {
                "score_crediticio": 760,
                "obligaciones_vigentes": 1,
                "dias_mora": 0,
                "cuotas_deuda_vigentes": 300000,
            },
            "de_cache": False,
            "degradado": False,
        },
        {
            "fuente": "cuentas-bancarias",
            "datos": {
                "saldo_promedio_3m": 6000000,
                "productos_activos": 2,
                "ingresos_mensuales": [6000000] * 6,
                "gastos_mensuales_promedio": 2500000,
                "sobregiros_6m": 0,
                "antiguedad_cuentas_meses": 48,
            },
            "de_cache": False,
            "degradado": False,
        },
    ]

    d = evaluar_fuentes_preaprobacion(fuentes_acl, D("12000000"), 24)

    assert d.estado == APROBADO
    assert d.monto_aprobado == D("12000000")
    assert d.score is not None and d.score >= 800
