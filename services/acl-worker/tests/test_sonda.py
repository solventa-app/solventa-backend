"""La sonda es la única que debe pagar el costo de probar la recuperación del
circuito (regla de arquitectura #5) — nunca una petición de usuario."""

import asyncio
import time

import pytest

from app.application.circuitos import construir_fabrica
from app.application.sonda import SondaRecuperacion


def _abrir_circuito(fabrica, nombre: str) -> None:
    with pytest.raises(RuntimeError), fabrica.get_breaker(nombre):
        raise RuntimeError("boom")


def test_no_sondea_si_el_circuito_esta_cerrado() -> None:
    fabrica = construir_fabrica(umbral_fallos=10, ttl_segundos=10)
    llamadas = []
    sonda = SondaRecuperacion(fabrica, sondeos={"x": lambda: llamadas.append(1)}, intervalo_s=1)

    sonda._intentar("x", lambda: llamadas.append(1))

    assert llamadas == []


def test_no_gasta_red_si_el_circuito_abierto_aun_no_vence_su_ttl() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=10)
    _abrir_circuito(fabrica, "x")
    llamadas = []
    sonda = SondaRecuperacion(fabrica, sondeos={"x": lambda: llamadas.append(1)}, intervalo_s=1)

    sonda._intentar("x", lambda: llamadas.append(1))

    assert llamadas == []  # fail-fast: el sondeo real nunca se ejecutó
    assert fabrica.get_breaker("x").context.state == "opened"


def test_sondea_de_verdad_y_cierra_el_circuito_cuando_el_ttl_ya_vencio() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=0.05)
    _abrir_circuito(fabrica, "x")
    time.sleep(0.1)
    llamadas = []
    sonda = SondaRecuperacion(fabrica, sondeos={"x": lambda: llamadas.append(1)}, intervalo_s=1)

    sonda._intentar("x", lambda: llamadas.append(1))

    assert llamadas == [1]  # la sonda SÍ pagó el costo de la llamada real
    assert fabrica.get_breaker("x").context.state == "closed"


def test_si_el_proveedor_sigue_caido_el_circuito_queda_abierto() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=0.05)
    _abrir_circuito(fabrica, "x")
    time.sleep(0.1)

    def sondeo_que_falla() -> None:
        raise ConnectionError("aun caido")

    sonda = SondaRecuperacion(fabrica, sondeos={"x": sondeo_que_falla}, intervalo_s=1)
    sonda._intentar("x", sondeo_que_falla)

    assert fabrica.get_breaker("x").context.state == "opened"


def test_ciclo_de_vida_arranca_y_se_puede_detener_sin_fallar() -> None:
    async def ejercicio() -> None:
        fabrica = construir_fabrica(umbral_fallos=10, ttl_segundos=10)
        sonda = SondaRecuperacion(fabrica, sondeos={"x": lambda: None}, intervalo_s=0.01)
        sonda.iniciar()
        await asyncio.sleep(0.03)
        await sonda.detener()

    asyncio.run(ejercicio())
