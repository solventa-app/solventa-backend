from datetime import UTC, datetime

from fakes import AHORA, repositorio_con_datos

from app.application.servicio_siniestros import ServicioSiniestros


def _servicio():
    return ServicioSiniestros(repositorio_con_datos(), reloj=lambda: AHORA)


def test_lista_del_mas_reciente_al_mas_antiguo():
    assert [s["id"] for s in _servicio().listar()] == ["sin-02", "sin-01", "sin-03"]


def test_filtra_por_estado():
    assert [s["id"] for s in _servicio().listar("EN_REVISION")] == ["sin-02"]


def test_expediente_trae_evidencias_y_verificaciones():
    s = _servicio().obtener("sin-01")
    assert s["asegurado"]["nombre"] == "Camilo Rojas"
    assert s["evidencias"][0]["archivo"] == "sin-01-foto-1.jpg"
    assert s["evidencias"][0]["archivo_id"] == "arch-1"
    assert s["evidencias"][0]["geoetiquetada"] is True
    assert s["verificaciones"] == [{"descripcion": "Póliza vigente", "cumple": True}]
    assert s["resolucion"] is None


def test_derivado_sale_como_resolucion_derivacion_con_su_perito():
    r = _servicio().obtener("sin-02")["resolucion"]
    assert r["tipo"] == "DERIVACION"
    assert r["perito"]["id"] == "per-01"
    assert r["instrucciones"] == "Revisar el bumper"
    assert r["vencimiento_informe"] == AHORA


def test_resuelto_trae_la_resolucion_final():
    r = _servicio().obtener("sin-03")["resolucion"]
    assert r["tipo"] == "APROBACION"
    assert r["indemnizacion"] == {"valor": "8000000", "moneda": "COP"}
    assert r["perito"] is None


def test_inexistente_es_none():
    assert _servicio().obtener("sin-99") is None


def test_parametricos_de_polizas_solo_trae_origen_parametrico():
    """KAN-259 Fase 5: `sin-01`/`sin-02`/`sin-03` (de la semilla) no son paramétricos, así que no
    salen aunque su póliza esté en la lista."""
    repo = repositorio_con_datos()
    repo.siniestros["sin-par-1"] = {
        "id": "sin-par-1",
        "radicado": "SIN-2026-1001",
        "poliza_id": "pol-01",
        "estado": "APROBADO",
        "asegurado": {"nombre": "Cobertura paramétrica", "documento": "N/A"},
        "cobertura_aplicada": "Cobertura paramétrica de vuelo",
        "entidad_acreedora": "Por confirmar con el asegurado",
        "ocurrido_en": AHORA,
        "notificado_en": AHORA,
        "ubicacion": {"tipo": "VUELO", "clave": "AV9340:2026-11-11"},
        "descripcion": "Siniestro paramétrico automático.",
        "suma_reclamada": {"valor": "500000", "moneda": "COP"},
        "vence_dictamen_en": AHORA,
        "origen": "PARAMETRICO",
        "evento_parametrico_id": "evt-1",
        "evento_valor": 145.0,
        "evento_umbral": 120,
        "evento_variable": None,
        "evento_cancelado": False,
        "vigencia_hasta_cobertura": AHORA,
    }
    servicio = ServicioSiniestros(repo, reloj=lambda: AHORA)
    movimientos = servicio.parametricos_de_polizas(["pol-01"])
    assert [m["id"] for m in movimientos] == ["sin-par-1"]
    assert movimientos[0]["origen"] == "PARAMETRICO"
    assert movimientos[0]["evento_valor"] == 145.0


def test_parametricos_de_polizas_vacio_sin_ids():
    assert _servicio().parametricos_de_polizas([]) == []


def test_peritos_solo_activos_con_vencimiento_en_dias_habiles():
    peritos = _servicio().peritos_para("sin-01")
    assert [p["id"] for p in peritos] == ["per-01"]
    # Miércoles 28-oct + 4 hábiles = martes 3-nov, 5:00 p. m. hora Colombia.
    assert peritos[0]["vencimiento_informe"].isoformat() == "2026-11-03T17:00:00-05:00"


def test_peritos_de_un_caso_inexistente_es_lista_vacia():
    assert _servicio().peritos_para("sin-99") == []


def test_lectura_normal_no_va_a_la_primaria():
    repo = repositorio_con_datos()
    ServicioSiniestros(repo, reloj=lambda: datetime.now(UTC)).obtener("sin-01")
    assert repo.lecturas_primaria == 0
