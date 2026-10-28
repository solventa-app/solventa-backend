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
