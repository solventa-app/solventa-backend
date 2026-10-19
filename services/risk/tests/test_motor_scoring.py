"""Pruebas unitarias completas del motor de scoring de riesgo (HU-W02 / KAN-28)."""

from decimal import Decimal

from app.application.motor_scoring import (
    PerfilFinancieroEntrada,
    calcular_score_motor,
    calcular_variables,
    clasificar_riesgo,
    extraer_perfil_desde_fuentes,
)


def _perfil_excelente() -> PerfilFinancieroEntrada:
    return PerfilFinancieroEntrada(
        moneda="COP",
        ingresos_mensuales=[Decimal("8000000")] * 6,
        gastos_mensuales_promedio=Decimal("3000000"),  # ratio_gasto = 0.375 (<= 0.60)
        cuotas_deuda_vigentes=Decimal("400000"),  # DTI = 0.05 (< 0.10)
        saldo_promedio_90d=Decimal("12000000"),  # colchon = 1.5 (>= 1.0)
        score_bureau=820,  # >= 750
        dias_mora=0,
        sobregiros_6m=0,
        antiguedad_cuentas_meses=48,  # >= 36
        numero_cuentas=3,
        productos_activos=3,
    )


def test_scoring_cliente_excelente_obtiene_alto_puntaje_y_riesgo_bajo() -> None:
    perfil = _perfil_excelente()
    resultado = calcular_score_motor(perfil)

    assert resultado.score >= 800
    assert resultado.clasificacion == "BAJO"
    assert resultado.nivel_riesgo == "A"
    assert resultado.score_normalizado >= 80.0
    assert not resultado.es_preliminar

    codigos = {r.codigo for r in resultado.razones}
    assert "DTI_BAJO" in codigos
    assert "INGRESO_ESTABLE" in codigos
    assert "GASTO_CONTROLADO" in codigos
    assert "LIQUIDEZ_ALTA" in codigos
    assert "SIN_SOBREGIROS" in codigos
    assert "ANTIGUEDAD_ALTA" in codigos
    assert "BUREAU_EXCELENTE" in codigos
    assert "SIN_MORA" in codigos


def test_scoring_desglose_por_cuatro_factores_obligatorios() -> None:
    perfil = _perfil_excelente()
    resultado = calcular_score_motor(perfil)

    desglose = resultado.desglose_por_factor
    assert set(desglose.keys()) == {"bureau", "open_finance", "ingresos", "dti"}

    # Cada factor debe tener puntos calculados y razones asociadas
    for factor in ("bureau", "open_finance", "ingresos", "dti"):
        fd = desglose[factor]
        assert fd.puntos > 0
        assert len(fd.razones) >= 1
        assert all(r.factor == factor for r in fd.razones)


def test_scoring_alto_dti_penaliza_fuertemente() -> None:
    # Cliente con deuda del 55% de sus ingresos
    perfil = PerfilFinancieroEntrada(
        ingresos_mensuales=[Decimal("4000000")] * 6,
        gastos_mensuales_promedio=Decimal("2000000"),
        cuotas_deuda_vigentes=Decimal("2200000"),  # DTI = 0.55
        saldo_promedio_90d=Decimal("500000"),
        score_bureau=680,
        dias_mora=0,
    )
    resultado = calcular_score_motor(perfil)

    razon_dti = next(r for r in resultado.razones if r.codigo == "DTI_ALTO")
    assert razon_dti.impacto == -150
    assert resultado.variables["dti_actual"] == 0.55


def test_scoring_mora_severa_penaliza_y_eleva_riesgo() -> None:
    perfil = PerfilFinancieroEntrada(
        ingresos_mensuales=[Decimal("5000000")] * 6,
        gastos_mensuales_promedio=Decimal("2500000"),
        cuotas_deuda_vigentes=Decimal("500000"),
        saldo_promedio_90d=Decimal("1000000"),
        score_bureau=520,
        dias_mora=75,  # Mora > 60 días
    )
    resultado = calcular_score_motor(perfil)

    razon_mora = next(r for r in resultado.razones if r.codigo == "MORA_SEVERA")
    assert razon_mora.impacto == -180
    assert resultado.clasificacion == "ALTO"


def test_scoring_mora_menor_genera_impacto_moderado() -> None:
    perfil = PerfilFinancieroEntrada(
        ingresos_mensuales=[Decimal("5000000")] * 6,
        gastos_mensuales_promedio=Decimal("2500000"),
        cuotas_deuda_vigentes=Decimal("500000"),
        saldo_promedio_90d=Decimal("2000000"),
        score_bureau=710,
        dias_mora=15,  # Mora <= 30 días
    )
    resultado = calcular_score_motor(perfil)

    razon_mora = next(r for r in resultado.razones if r.codigo == "MORA_MENOR")
    assert razon_mora.impacto == -50


def test_scoring_volatilidad_ingresos_y_gasto_excesivo() -> None:
    # Ingresos con alta dispersión y gastos superiores al 90%
    perfil = PerfilFinancieroEntrada(
        ingresos_mensuales=[
            Decimal("1000000"),
            Decimal("6000000"),
            Decimal("1500000"),
            Decimal("7000000"),
            Decimal("2000000"),
            Decimal("8000000"),
        ],  # cv > 0.25
        gastos_mensuales_promedio=Decimal("4200000"),  # > 90% del promedio (~4250000)
        cuotas_deuda_vigentes=Decimal("200000"),
        saldo_promedio_90d=Decimal("500000"),
        score_bureau=640,
    )
    resultado = calcular_score_motor(perfil)

    codigos = {r.codigo for r in resultado.razones}
    assert "INGRESO_VOLATIL" in codigos
    assert "GASTO_EXCESIVO" in codigos


def test_scoring_sin_liquidez_y_sobregiros_frecuentes() -> None:
    perfil = PerfilFinancieroEntrada(
        ingresos_mensuales=[Decimal("3000000")] * 6,
        gastos_mensuales_promedio=Decimal("2000000"),
        cuotas_deuda_vigentes=Decimal("300000"),
        saldo_promedio_90d=Decimal("50000"),  # Colchón < 0.1
        sobregiros_6m=4,  # Sobregiros > 2
        antiguedad_cuentas_meses=6,  # Menos de 1 año
    )
    resultado = calcular_score_motor(perfil)

    codigos = {r.codigo for r in resultado.razones}
    assert "SIN_LIQUIDEZ" in codigos
    assert "SOBREGIROS_FRECUENTES" in codigos
    assert "ANTIGUEDAD_BAJA" in codigos


def test_scoring_manejo_fuentes_parciales_sin_bureau() -> None:
    perfil = PerfilFinancieroEntrada(
        ingresos_mensuales=[Decimal("4000000")] * 6,
        gastos_mensuales_promedio=Decimal("2000000"),
        cuotas_deuda_vigentes=Decimal("400000"),
        saldo_promedio_90d=Decimal("2000000"),
        score_bureau=None,  # Bureau no disponible
        fuentes_degradadas=["historial-crediticio"],
        es_preliminar=True,
    )
    resultado = calcular_score_motor(perfil)

    codigos = {r.codigo for r in resultado.razones}
    assert "BUREAU_NO_DISPONIBLE" in codigos
    assert resultado.es_preliminar is True


def test_extraer_perfil_desde_fuentes_acl_completas() -> None:
    fuentes_acl = [
        {
            "fuente": "historial-crediticio",
            "datos": {
                "score_crediticio": 745,
                "obligaciones_vigentes": 2,
                "dias_mora": 0,
                "cuotas_deuda_vigentes": 350000,
            },
            "de_cache": False,
            "degradado": False,
        },
        {
            "fuente": "cuentas-bancarias",
            "datos": {
                "saldo_promedio_3m": 5000000,
                "productos_activos": 3,
                "sobregiros_6m": 0,
                "antiguedad_cuentas_meses": 40,
                "ingresos_mensuales": [5000000] * 6,
                "gastos_mensuales_promedio": 2500000,
            },
            "de_cache": False,
            "degradado": False,
        },
        {
            "fuente": "afiliacion-pila",
            "datos": {"estado_afiliacion": "activo", "antiguedad_meses": 36},
            "de_cache": False,
            "degradado": False,
        },
    ]

    perfil = extraer_perfil_desde_fuentes(fuentes_acl)
    assert perfil.score_bureau == 745
    assert perfil.dias_mora == 0
    assert perfil.cuotas_deuda_vigentes == Decimal("350000")
    assert perfil.saldo_promedio_90d == Decimal("5000000")
    assert perfil.antiguedad_cuentas_meses == 40
    assert not perfil.es_preliminar


def test_extraer_perfil_con_fuente_degradada_marca_preliminar() -> None:
    fuentes_parciales = [
        {
            "fuente": "historial-crediticio",
            "datos": {},
            "de_cache": False,
            "degradado": True,  # Falló historial
        },
        {
            "fuente": "cuentas-bancarias",
            "datos": {"saldo_promedio_3m": 2500000, "productos_activos": 1},
            "de_cache": False,
            "degradado": False,
        },
    ]

    perfil = extraer_perfil_desde_fuentes(fuentes_parciales)
    assert perfil.es_preliminar is True
    assert "historial-crediticio" in perfil.fuentes_degradadas
    assert perfil.score_bureau is None


def test_score_acotado_estrictamente_entre_0_y_1000() -> None:
    # Caso peor posible
    perfil_pesimo = PerfilFinancieroEntrada(
        ingresos_mensuales=[Decimal("1000000")] * 6,
        gastos_mensuales_promedio=Decimal("2000000"),
        cuotas_deuda_vigentes=Decimal("2000000"),  # DTI = 2.0
        saldo_promedio_90d=Decimal("0"),
        score_bureau=200,
        dias_mora=120,
        sobregiros_6m=10,
        antiguedad_cuentas_meses=1,
    )
    resultado_bajo = calcular_score_motor(perfil_pesimo)
    assert resultado_bajo.score >= 0
    assert resultado_bajo.clasificacion == "ALTO"

    # Caso excelente
    perfil_tope = _perfil_excelente()
    resultado_alto = calcular_score_motor(perfil_tope)
    assert resultado_alto.score <= 1000


def test_clasificar_riesgo_rangos() -> None:
    assert clasificar_riesgo(850) == ("BAJO", "A")
    assert clasificar_riesgo(700) == ("MEDIO", "B")
    assert clasificar_riesgo(600) == ("MEDIO", "C")
    assert clasificar_riesgo(500) == ("ALTO", "D")
    assert clasificar_riesgo(400) == ("ALTO", "E")


def test_calcular_variables_ingreso_vacio_no_lanza_excepcion() -> None:
    perfil_vacio = PerfilFinancieroEntrada(ingresos_mensuales=[])
    vars_calc = calcular_variables(perfil_vacio)
    assert vars_calc["dti_actual"] == 1.0
