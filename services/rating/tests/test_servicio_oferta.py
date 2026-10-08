"""Pruebas unitarias del cálculo de score/prima/oferta (T-W01-6 completo,
HA-MOD-002, HA-LAT-001) — sin MongoDB ni Redis reales (dobles de prueba)."""

from fakes import AlmacenClaveValorFalso, LectorPerfilesFalso

from app.application import servicio_oferta as modulo_oferta
from app.application.cache_score import CacheScore
from app.application.servicio_oferta import ServicioOferta, calcular_score

# Mismos valores por defecto que devuelven los stubs (ver
# stubs/open-finance/app/main.py y stubs/open-data/app/main.py) para que el
# caso "sano" de estas pruebas sea reproducible también al verificar en vivo.
FUENTES_SANAS = [
    {
        "fuente": "historial-crediticio",
        "datos": {"score_crediticio": 712, "obligaciones_vigentes": 1},
        "capturado_en": "2026-10-08T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
    {
        "fuente": "cuentas-bancarias",
        "datos": {"saldo_promedio_3m": 4_250_000, "productos_activos": 2},
        "capturado_en": "2026-10-08T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
    {
        "fuente": "afiliacion-pila",
        "datos": {"estado_afiliacion": "activo", "antiguedad_meses": 36},
        "capturado_en": "2026-10-08T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
    {
        "fuente": "camara-comercio",
        "datos": {"existe_registro": True, "actividad_economica": "otros servicios"},
        "capturado_en": "2026-10-08T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
    {
        "fuente": "antecedentes-judiciales",
        "datos": {"antecedentes_vigentes": False},
        "capturado_en": "2026-10-08T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    },
]

PERFIL_SANO = {
    "cliente_id": "cliente-1",
    "perfil_version": 1,
    "fuentes": FUENTES_SANAS,
    "leido_de_secundaria": True,
}


def _perfil_con_fuentes(fuentes: list[dict], version: int = 1) -> dict:
    return {
        "cliente_id": "cliente-1",
        "perfil_version": version,
        "fuentes": fuentes,
        "leido_de_secundaria": True,
    }


# ---------- calcular_score: números exactos ----------


def test_score_con_todas_las_fuentes_sanas_da_el_numero_exacto_de_la_formula() -> None:
    score_total, desglose = calcular_score(FUENTES_SANAS)

    # 0.30*70.25 + 0.20*88 + 0.20*88 + 0.15*70 + 0.15*100 = 81.775 -> round -> 82
    assert score_total == 82
    assert desglose == [
        {"nombre": "historial-crediticio", "peso": 0.30},
        {"nombre": "cuentas-bancarias", "peso": 0.20},
        {"nombre": "afiliacion-pila", "peso": 0.20},
        {"nombre": "camara-comercio", "peso": 0.15},
        {"nombre": "antecedentes-judiciales", "peso": 0.15},
    ]


def test_fuente_faltante_usa_el_neutro_50_no_excluye_del_promedio() -> None:
    sin_camara = [f for f in FUENTES_SANAS if f["fuente"] != "camara-comercio"]

    score_total, _ = calcular_score(sin_camara)

    # Igual que el caso sano pero con camara-comercio en 50 en vez de 70:
    # 81.775 - 0.15*70 + 0.15*50 = 78.775 -> round -> 79
    assert score_total == 79


def test_fuente_con_datos_vacios_usa_el_neutro_50() -> None:
    con_datos_vacios = [
        {**f, "datos": {}} if f["fuente"] == "camara-comercio" else f for f in FUENTES_SANAS
    ]

    score_total, _ = calcular_score(con_datos_vacios)

    assert score_total == 79  # mismo resultado que la fuente ausente


def test_fuente_degradada_usa_el_neutro_50_aunque_no_falte_en_la_lista() -> None:
    degradada = [
        {**f, "datos": {}, "degradado": True} if f["fuente"] == "historial-crediticio" else f
        for f in FUENTES_SANAS
    ]

    score_total, _ = calcular_score(degradada)

    # 81.775 - 0.30*70.25 + 0.30*50 = 75.7 -> round -> 76
    assert score_total == 76


# ---------- ServicioOferta.generar: oferta completa vs preliminar ----------


def test_oferta_completa_con_prima_puntual_cuando_todas_las_fuentes_estan_frescas() -> None:
    lector = LectorPerfilesFalso(perfil=PERFIL_SANO)
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    oferta, score_de_cache = servicio.generar("cliente-1", "1700000001.1", "200000000")

    assert score_de_cache is False
    assert oferta["tipo"] == "COMPLETA"
    assert oferta["rangoPrima"] is None
    assert oferta["prima"] == {"valor": "94680.00", "moneda": "COP"}
    assert oferta["cobertura"] == {"valor": "200000000", "moneda": "COP"}
    assert len(oferta["fuentes"]) == 5
    assert all(f["estado"] == "OK" for f in oferta["fuentes"])
    origenes = {f["nombre"]: f["origen"] for f in oferta["fuentes"]}
    assert origenes["cuentas-bancarias"] == "OPEN_FINANCE"
    assert origenes["historial-crediticio"] == "OPEN_FINANCE"
    assert origenes["afiliacion-pila"] == "OPEN_DATA"
    assert origenes["camara-comercio"] == "OPEN_DATA"
    assert origenes["antecedentes-judiciales"] == "OPEN_DATA"


def test_oferta_preliminar_con_rango_cuando_una_fuente_esta_degradada() -> None:
    degradada = [
        {**f, "datos": {}, "degradado": True} if f["fuente"] == "historial-crediticio" else f
        for f in FUENTES_SANAS
    ]
    lector = LectorPerfilesFalso(perfil=_perfil_con_fuentes(degradada))
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    oferta, _ = servicio.generar("cliente-1", "1700000001.1", "200000000")

    assert oferta["tipo"] == "PRELIMINAR"
    assert oferta["prima"] is None
    assert oferta["rangoPrima"] == {
        "minima": {"valor": "81792.00", "moneda": "COP"},
        "maxima": {"valor": "122688.00", "moneda": "COP"},
    }
    fuente_fallo = next(f for f in oferta["fuentes"] if f["nombre"] == "historial-crediticio")
    assert fuente_fallo["estado"] == "FALLO"
    assert fuente_fallo["capturadoEn"] is None


def test_oferta_preliminar_cuando_una_fuente_viene_de_cache() -> None:
    de_cache = [
        {**f, "de_cache": True} if f["fuente"] == "cuentas-bancarias" else f
        for f in FUENTES_SANAS
    ]
    lector = LectorPerfilesFalso(perfil=_perfil_con_fuentes(de_cache))
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    oferta, _ = servicio.generar("cliente-1", "1700000001.1", "200000000")

    assert oferta["tipo"] == "PRELIMINAR"
    fuente_cache = next(f for f in oferta["fuentes"] if f["nombre"] == "cuentas-bancarias")
    assert fuente_cache["estado"] == "CACHE"
    assert fuente_cache["capturadoEn"] == "2026-10-08T12:00:00+00:00"


def test_perfil_inexistente_devuelve_none() -> None:
    lector = LectorPerfilesFalso(perfil=None)
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    oferta, score_de_cache = servicio.generar("sin-perfil", "1.0", "200000000")

    assert oferta is None
    assert score_de_cache is False


# ---------- Cache-Aside del score (HA-LAT-001) ----------


def test_segunda_consulta_del_mismo_perfil_sale_del_cache() -> None:
    lector = LectorPerfilesFalso(perfil=PERFIL_SANO)
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    _, primera_de_cache = servicio.generar("cliente-1", "1700000001.1", "200000000")
    _, segunda_de_cache = servicio.generar("cliente-1", "1700000001.1", "200000000")

    assert primera_de_cache is False  # primera vez: se calcula y se guarda
    assert segunda_de_cache is True  # segunda vez: mismo perfil_version -> hit


def test_cobertura_distinta_en_la_segunda_consulta_sigue_dando_la_prima_correcta() -> None:
    """El score se cachea, pero la prima SIEMPRE se recalcula con la
    `cobertura` recibida — nunca se devuelve la prima de otra solicitud."""
    lector = LectorPerfilesFalso(perfil=PERFIL_SANO)
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    oferta_1, _ = servicio.generar("cliente-1", "1700000001.1", "200000000")
    oferta_2, de_cache_2 = servicio.generar("cliente-1", "1700000001.1", "100000000")

    assert de_cache_2 is True  # el score sí vino de caché
    assert oferta_1["prima"] == {"valor": "94680.00", "moneda": "COP"}
    assert oferta_2["prima"] == {"valor": "47340.00", "moneda": "COP"}  # mitad de cobertura


def test_cambiar_version_de_reglas_invalida_el_hit_de_cache(monkeypatch) -> None:
    """Verificación explícita pedida por la tarea: subir `VERSION_REGLAS`
    (p. ej. al cambiar pesos/fórmula) deja la caché vieja huérfana — la
    siguiente consulta NO es un hit, aunque sea el mismo perfil."""
    lector = LectorPerfilesFalso(perfil=PERFIL_SANO)
    servicio = ServicioOferta(lector, CacheScore(AlmacenClaveValorFalso(), ttl_segundos=300))

    _, primera_de_cache = servicio.generar("cliente-1", "1700000001.1", "200000000")
    assert primera_de_cache is False

    _, segunda_de_cache = servicio.generar("cliente-1", "1700000001.1", "200000000")
    assert segunda_de_cache is True  # confirma que sin cambiar versión sí es hit

    monkeypatch.setattr(modulo_oferta, "VERSION_REGLAS", "v2")

    _, tercera_de_cache = servicio.generar("cliente-1", "1700000001.1", "200000000")
    assert tercera_de_cache is False  # version distinta -> clave distinta -> no hit
