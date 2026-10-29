from datetime import UTC, datetime

from app.application.dias_habiles import sumar_dias_habiles


def test_salta_el_fin_de_semana():
    viernes = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)
    assert sumar_dias_habiles(viernes, 1).isoformat() == "2026-11-02T17:00:00-05:00"


def test_cero_dias_es_hoy_a_las_cinco():
    lunes = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
    assert sumar_dias_habiles(lunes, 0).isoformat() == "2026-10-26T17:00:00-05:00"
