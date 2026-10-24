"""Sonda disparada desde fuera (Cloud Scheduler, decisión W05-7)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from test_main import CONFIG_SIN_PROVEEDORES

from app.application.circuitos import construir_fabrica
from app.application.configuracion import Configuracion
from app.application.sonda import SondaRecuperacion
from app.main import create_app


def _abrir(fabrica, nombre: str) -> None:
    with pytest.raises(RuntimeError), fabrica.get_breaker(nombre):
        raise RuntimeError("boom")


def test_una_pasada_sondea_y_cierra_el_circuito_recuperado() -> None:
    fabrica = construir_fabrica(umbral_fallos=1, ttl_segundos=0.05)
    _abrir(fabrica, "x")
    time.sleep(0.1)
    llamadas: list[int] = []
    sonda = SondaRecuperacion(fabrica, {"x": lambda: llamadas.append(1)}, intervalo_s=1)

    estados = asyncio.run(sonda.una_pasada())

    assert llamadas == [1]
    assert estados == {"x": "closed"}


def test_una_pasada_con_los_circuitos_sanos_no_gasta_ninguna_llamada() -> None:
    fabrica = construir_fabrica(umbral_fallos=3, ttl_segundos=5)
    llamadas: list[int] = []
    sonda = SondaRecuperacion(fabrica, {"x": lambda: llamadas.append(1)}, intervalo_s=1)

    assert asyncio.run(sonda.una_pasada()) == {"x": "closed"}
    assert llamadas == []


def test_el_endpoint_ejecuta_una_pasada_y_devuelve_los_estados() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/sonda/ejecutar")

    assert r.status_code == 200
    assert set(r.json().values()) == {"closed"}
    assert len(r.json()) == 5  # Open Finance, Open Data, la pasarela, la firma y el KYC


def test_con_disparador_externo_no_se_arranca_el_bucle(monkeypatch) -> None:
    iniciadas: list[bool] = []
    original = SondaRecuperacion.iniciar
    monkeypatch.setattr(
        SondaRecuperacion, "iniciar", lambda self: (iniciadas.append(True), original(self))
    )
    for disparador, esperado in (("bucle", [True]), ("externo", [])):
        iniciadas.clear()
        config = Configuracion(**{**CONFIG_SIN_PROVEEDORES.__dict__, "disparador": disparador})
        with TestClient(create_app(config)):
            pass
        assert iniciadas == esperado


def test_la_configuracion_valida_el_disparador_y_el_proveedor_de_pasarela(monkeypatch) -> None:
    monkeypatch.setenv("DISPARADOR", "magia")
    with pytest.raises(ValueError, match="DISPARADOR"):
        Configuracion.desde_entorno()
    monkeypatch.setenv("DISPARADOR", "bucle")
    monkeypatch.setenv("PROVEEDOR_PASARELA", "otra")
    with pytest.raises(ValueError, match="PROVEEDOR_PASARELA"):
        Configuracion.desde_entorno()
    monkeypatch.setenv("PROVEEDOR_PASARELA", "alterna")
    config = Configuracion.desde_entorno()
    assert (config.disparador, config.proveedor_pasarela) == ("bucle", "alterna")


def test_el_proveedor_alterno_se_elige_por_configuracion() -> None:
    config = Configuracion(**{**CONFIG_SIN_PROVEEDORES.__dict__, "proveedor_pasarela": "alterna"})
    with TestClient(create_app(config)) as client:
        r = client.post(
            "/pagos/cobrar",
            json={
                "id_intento_cobro": "i-1",
                "poliza_id": "p-1",
                "monto": "1.00",
                "moneda": "COP",
                "token_medio_pago": "tok",
            },
        )
    assert r.status_code == 200
    assert r.json()["estado"] == "pendiente"  # sin proveedor real detrás: degrada, nunca 5xx
