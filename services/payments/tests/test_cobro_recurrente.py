"""W05-A6 y A7: cobro por ciclo con el medio guardado, y aviso de cada cambio de estado."""

import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.avisos import CANAL_COBROS, BusConAvisos
from app.application.bus_eventos import construir_evento_cobro_estado_cambiado
from app.application.cobro_recurrente import (
    RepositorioProgramacionesPostgres,
    ServicioCobroRecurrente,
    id_intento_ciclo,
)
from app.application.configuracion import Configuracion
from app.application.repositorio_cobros import construir_pool

AHORA = datetime(2026, 11, 1, tzinfo=UTC)


def _evento() -> dict[str, Any]:
    return construir_evento_cobro_estado_cambiado("c-1", "p-1", "cobrado", "i-1")


class _Bus:
    def __init__(self) -> None:
        self.eventos: list[dict] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        self.eventos.append(evento)


def test_el_aviso_va_al_canal_de_cobros_sin_el_intento() -> None:
    class Redis:
        publicados: list[tuple[str, str]] = []

        def publish(self, canal: str, mensaje: str) -> None:
            self.publicados.append((canal, mensaje))

    bus = _Bus()
    BusConAvisos(bus, Redis()).publicar(_evento())

    assert len(bus.eventos) == 1
    canal, mensaje = Redis.publicados[0]
    assert canal == CANAL_COBROS
    assert json.loads(mensaje) == {
        "tipo": "cobro",
        "cobroId": "c-1",
        "polizaId": "p-1",
        "estado": "COBRADO",
    }


def test_redis_caido_no_afecta_al_cobro() -> None:
    class Redis:
        def publish(self, canal: str, mensaje: str) -> None:
            raise ConnectionError

    bus = _Bus()
    BusConAvisos(bus, Redis()).publicar(_evento())

    assert len(bus.eventos) == 1


@pytest.mark.parametrize("valor", ["0", "32"])
def test_el_intervalo_fuera_de_rango_no_arranca(monkeypatch, valor: str) -> None:
    monkeypatch.setenv("COBRO_RECURRENTE_INTERVALO_DIAS", valor)

    with pytest.raises(ValueError, match="COBRO_RECURRENTE_INTERVALO_DIAS"):
        Configuracion.desde_entorno()


def test_el_intervalo_es_30_por_omision(monkeypatch) -> None:
    monkeypatch.delenv("COBRO_RECURRENTE_INTERVALO_DIAS", raising=False)

    assert Configuracion.desde_entorno().cobro_recurrente_intervalo_dias == 30


def test_el_intento_es_determinista_y_distinto_por_ciclo() -> None:
    assert id_intento_ciclo("p", 1) == id_intento_ciclo("p", 1)
    assert id_intento_ciclo("p", 1) != id_intento_ciclo("p", 2)


def test_un_cobro_que_falla_no_frena_a_los_demas() -> None:
    class Repositorio:
        def reservar_ciclos(self, ahora: datetime, lote: int) -> list[dict]:
            return [{"id": "1", "poliza_id": "a"}, {"id": "2", "poliza_id": "b"}]

    class Cobros:
        ejecutados: list[str] = []

        def ejecutar_cobro(self, cobro: dict) -> dict:
            self.ejecutados.append(cobro["id"])
            if cobro["id"] == "1":
                raise TimeoutError
            return cobro

    assert ServicioCobroRecurrente(Repositorio(), Cobros(), 30).generar(AHORA) == 2
    assert Cobros.ejecutados == ["1", "2"]


def test_los_endpoints_sin_servicio_responden_404() -> None:
    from fakes import ClienteAclFalso, RepositorioCobrosFalso

    from app.application.bus_eventos import BusEventosLog
    from app.application.servicio_cobros import ServicioCobros
    from app.main import create_app

    servicio = ServicioCobros(RepositorioCobrosFalso(), ClienteAclFalso(), BusEventosLog())
    with TestClient(create_app(servicio_cobros=servicio)) as cliente:
        assert cliente.post("/cobros/_generar-recurrentes").status_code == 404


# ---------- Contra PostgreSQL real, si `PAYMENTS_TEST_DATABASE_URL` da una base migrada ----------

URL_PRUEBA = os.getenv("PAYMENTS_TEST_DATABASE_URL")
con_postgres = pytest.mark.skipif(not URL_PRUEBA, reason="sin PAYMENTS_TEST_DATABASE_URL")


@pytest.fixture
def pool():
    pool = construir_pool(URL_PRUEBA)
    yield pool
    pool.close()


def _primer_cobro(pool, creado_en: datetime) -> str:
    propuesta, medio = str(uuid.uuid4()), str(uuid.uuid4())
    with pool.connection() as conexion:
        conexion.execute(
            "INSERT INTO medios_pago (id, propuesta_id, token_cifrado, marca, tipo) "
            "VALUES (%s, %s, 'tok', 'VISA', 'CREDITO')",
            (medio, propuesta),
        )
        conexion.execute(
            "INSERT INTO cobros (id, id_intento_cobro, poliza_id, monto, moneda, "
            "token_medio_pago, estado, origen, medio_pago_id, creado_en) "
            "VALUES (%s, %s, %s, '100', 'COP', 'tok', 'cobrado', 'INTERACTIVO', %s, %s)",
            (str(uuid.uuid4()), str(uuid.uuid4()), propuesta, medio, creado_en),
        )
    return propuesta


def _ciclos(pool, propuesta: str) -> list[str]:
    with pool.connection() as conexion:
        filas = conexion.execute(
            "SELECT id_intento_cobro FROM cobros WHERE poliza_id = %s AND origen = 'RECURRENTE'",
            (propuesta,),
        ).fetchall()
    return [f["id_intento_cobro"] for f in filas]


@con_postgres
def test_postgres_alta_idempotente_y_sin_medio_no_programa(pool) -> None:
    repositorio = RepositorioProgramacionesPostgres(pool)
    propuesta = _primer_cobro(pool, AHORA)
    vigencia = AHORA + timedelta(days=365)

    assert repositorio.crear(propuesta, "poliza", vigencia, 30) is True
    assert repositorio.crear(propuesta, "poliza", vigencia, 1) is False
    assert repositorio.crear("sin-medio", "poliza", vigencia, 30) is False


@con_postgres
def test_postgres_un_cobro_por_ciclo_y_se_detiene_en_la_vigencia(pool) -> None:
    repositorio = RepositorioProgramacionesPostgres(pool)
    propuesta = _primer_cobro(pool, AHORA)
    repositorio.crear(propuesta, "poliza", AHORA + timedelta(days=2), 1)

    # Lote grande: las filas de otras pruebas no deben dejar fuera a esta.
    primero = repositorio.reservar_ciclos(AHORA + timedelta(days=1), 10_000)
    assert propuesta in {c["poliza_id"] for c in primero}
    assert repositorio.reservar_ciclos(AHORA + timedelta(hours=25), 10_000) == []
    repositorio.reservar_ciclos(AHORA + timedelta(days=2), 10_000)
    repositorio.reservar_ciclos(AHORA + timedelta(days=90), 10_000)

    assert sorted(_ciclos(pool, propuesta)) == sorted(
        [id_intento_ciclo(propuesta, 1), id_intento_ciclo(propuesta, 2)]
    )


@con_postgres
def test_postgres_dos_pasadas_a_la_vez_no_duplican(pool) -> None:
    repositorio = RepositorioProgramacionesPostgres(pool)
    propuesta = _primer_cobro(pool, AHORA)
    repositorio.crear(propuesta, "poliza", AHORA + timedelta(days=365), 1)
    momento = AHORA + timedelta(days=1)
    hilos = [
        threading.Thread(target=repositorio.reservar_ciclos, args=(momento, 10_000))
        for _ in range(4)
    ]

    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert _ciclos(pool, propuesta) == [id_intento_ciclo(propuesta, 1)]
