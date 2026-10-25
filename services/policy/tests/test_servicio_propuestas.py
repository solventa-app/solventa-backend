"""Aceptar una oferta COMPLETA (HU-W03, CA-W03-01)."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fakes import RatingFalso, RepositorioPropuestasFalso, oferta_de_rating

from app.application.servicio_propuestas import (
    OFERTA_NO_ENCONTRADA,
    OFERTA_PRELIMINAR,
    OFERTA_VENCIDA,
    OfertaNoAceptable,
    ServicioPropuestas,
)


def _servicio(rating: RatingFalso | None = None) -> tuple[ServicioPropuestas, RatingFalso]:
    rating = rating or RatingFalso()
    return ServicioPropuestas(RepositorioPropuestasFalso(), rating), rating


def test_acepta_una_oferta_completa_con_el_precio_de_rating() -> None:
    servicio, _ = _servicio()

    propuesta = servicio.aceptar("oferta-1", "usuario-1")

    assert propuesta["estado"] == "ACEPTADA"
    assert propuesta["prima"] == {"valor": "185000", "moneda": "COP"}
    assert propuesta["cobertura"] == {"valor": "200000000", "moneda": "COP"}
    assert propuesta["plazo_meses"] == 60
    assert propuesta["cliente_id"] == "cliente-opaco"
    assert propuesta["actor_usuario_id"] == "usuario-1"


def test_la_propuesta_guarda_la_version_de_reglas_y_el_consentimiento_de_la_oferta() -> None:
    # CA-W03-01: se guarda la oferta aceptada: prima, cobertura, versión de reglas y consentimiento.
    servicio, _ = _servicio(RatingFalso({"oferta-1": oferta_de_rating(versionReglas="v7")}))

    propuesta = servicio.aceptar("oferta-1", "usuario-1")

    assert propuesta["version_reglas"] == "v7"
    assert propuesta["consentimiento_id"] == "consentimiento-1"
    assert propuesta["prima"] == {"valor": "185000", "moneda": "COP"}
    assert propuesta["cobertura"] == {"valor": "200000000", "moneda": "COP"}


def test_una_oferta_sin_version_de_reglas_se_acepta_y_queda_en_nulo() -> None:
    # Una oferta que RATING guardó antes de incluir la versión no debe impedir aceptarla.
    oferta = oferta_de_rating()
    del oferta["versionReglas"]
    servicio, _ = _servicio(RatingFalso({"oferta-1": oferta}))

    propuesta = servicio.aceptar("oferta-1", "usuario-1")

    assert propuesta["estado"] == "ACEPTADA"
    assert propuesta["version_reglas"] is None


def test_aceptar_dos_veces_devuelve_la_misma_propuesta_sin_volver_a_leer_a_rating() -> None:
    servicio, rating = _servicio()

    primera = servicio.aceptar("oferta-1", "usuario-1")
    segunda = servicio.aceptar("oferta-1", "usuario-1")

    assert primera["id"] == segunda["id"]
    assert rating.llamadas == 1


def test_aceptar_en_paralelo_crea_una_sola_propuesta() -> None:
    servicio, _ = _servicio()
    ids: list[str] = []

    def aceptar() -> None:
        ids.append(servicio.aceptar("oferta-1", "usuario-1")["id"])

    hilos = [threading.Thread(target=aceptar) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(ids) == 8
    assert len(set(ids)) == 1


def test_una_oferta_preliminar_no_se_acepta() -> None:
    oferta = oferta_de_rating(tipo="PRELIMINAR", prima=None)
    servicio, _ = _servicio(RatingFalso({"oferta-1": oferta}))

    with pytest.raises(OfertaNoAceptable) as error:
        servicio.aceptar("oferta-1", None)

    assert error.value.motivo == OFERTA_PRELIMINAR


def test_una_oferta_vencida_no_se_acepta() -> None:
    vencida = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    servicio, _ = _servicio(RatingFalso({"oferta-1": oferta_de_rating(vigenteHasta=vencida)}))

    with pytest.raises(OfertaNoAceptable) as error:
        servicio.aceptar("oferta-1", None)

    assert error.value.motivo == OFERTA_VENCIDA


def test_una_oferta_que_rating_no_tiene_no_se_acepta() -> None:
    servicio, _ = _servicio()

    with pytest.raises(OfertaNoAceptable) as error:
        servicio.aceptar("no-existe", None)

    assert error.value.motivo == OFERTA_NO_ENCONTRADA


def test_la_propuesta_guarda_el_nivel_de_riesgo_de_la_oferta() -> None:
    servicio, _ = _servicio()

    assert servicio.aceptar("oferta-1", "usuario-1")["nivel_riesgo"] == "BAJO"


def test_si_la_oferta_no_trae_perfil_de_riesgo_la_propuesta_lo_deja_vacio() -> None:
    oferta = oferta_de_rating()
    del oferta["perfilRiesgo"]
    servicio, _ = _servicio(RatingFalso({"oferta-1": oferta}))

    assert servicio.aceptar("oferta-1", "usuario-1")["nivel_riesgo"] is None
