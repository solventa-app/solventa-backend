"""Verifica el comportamiento perezoso de `purgatory` que explota la sonda
(ver `app/application/sonda.py`): el circuito abre tras N fallos, hace
fail-fast mientras el TTL no vence, y solo se reevalúa (half-open -> closed u
opened) en el PRÓXIMO `with breaker:`, nunca por sí solo con un temporizador."""

import time

import pytest

from app.application.circuitos import CircuitoAbiertoError, construir_fabrica


def test_abre_tras_el_umbral_de_fallos() -> None:
    fabrica = construir_fabrica(umbral_fallos=2, ttl_segundos=10)

    for _ in range(2):
        with pytest.raises(RuntimeError), fabrica.get_breaker("demo"):
            raise RuntimeError("boom")

    assert fabrica.get_breaker("demo").context.state == "opened"


def test_hace_fail_fast_mientras_el_ttl_no_vence() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=10)

    with pytest.raises(RuntimeError), fabrica.get_breaker("demo"):
        raise RuntimeError("boom")

    llamadas = []
    with pytest.raises(CircuitoAbiertoError), fabrica.get_breaker("demo"):
        llamadas.append(1)  # no debe ejecutarse: el breaker falla antes de entrar

    assert llamadas == []


def test_el_estado_no_cambia_solo_por_el_paso_del_tiempo() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=0.05)

    with pytest.raises(RuntimeError), fabrica.get_breaker("demo"):
        raise RuntimeError("boom")

    time.sleep(0.1)
    # Aunque el TTL ya venció, el estado persistido sigue "opened" hasta que
    # ALGUIEN vuelva a llamar: por eso la sonda, no un temporizador, es quien
    # debe disparar la próxima llamada.
    assert fabrica.get_breaker("demo").context.state == "opened"


def test_tras_el_ttl_la_siguiente_llamada_exitosa_cierra_el_circuito() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=0.05)

    with pytest.raises(RuntimeError), fabrica.get_breaker("demo"):
        raise RuntimeError("boom")

    time.sleep(0.1)
    with fabrica.get_breaker("demo"):
        pass  # esta es la llamada que paga el costo de la sonda

    assert fabrica.get_breaker("demo").context.state == "closed"
