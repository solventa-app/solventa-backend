import pytest

from app.application.esquema_evento_parametrico import (
    EventoInvalido,
    construir_evento_clima,
    construir_evento_vuelo,
    validar,
)


def test_evento_de_clima_es_valido() -> None:
    evento = construir_evento_clima(
        clave="4.61,-74.08",
        variable="PRECIPITACION_MM",
        valor=55.2,
        medido_en="2026-11-11T00:00:00+00:00",
    )
    validar(evento)  # no debe lanzar
    assert evento["datos"]["tipo"] == "CLIMA"
    assert "cancelado" not in evento["datos"]


def test_evento_de_vuelo_es_valido() -> None:
    evento = construir_evento_vuelo(
        numero_vuelo="AV9340",
        fecha="2026-11-11",
        retraso_minutos=145,
        cancelado=False,
        medido_en="2026-11-11T00:00:00+00:00",
    )
    validar(evento)  # no debe lanzar
    assert evento["datos"]["clave"] == "AV9340:2026-11-11"
    assert "variable" not in evento["datos"]


def test_un_evento_mal_formado_falla_la_validacion() -> None:
    evento = construir_evento_clima(
        clave="x", variable="PRECIPITACION_MM", valor=1.0, medido_en="2026-11-11T00:00:00+00:00"
    )
    del evento["datos"]["fuente"]
    with pytest.raises(EventoInvalido):
        validar(evento)
