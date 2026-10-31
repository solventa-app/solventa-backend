"""W01-A3 y A4: el perfil no sobrevive a su consentimiento."""

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from fakes import AlmacenClaveValorFalso, BusEventosFalso, RepositorioPerfilesFalso
from fastapi.testclient import TestClient

from app.application.cache_score import CacheScore, clave_oferta, clave_score
from app.application.repositorio_perfiles import RepositorioPerfilesMongo
from app.application.servicio_perfiles import ServicioPerfiles
from app.main import create_app

TITULAR = "titular-hmac-1234567890"
REVOCACION = {"titular_id": TITULAR, "consentimiento_id": "c-1"}
PERFIL = {"cliente_id": TITULAR, "perfil_version": 2, "version_reglas": "v1"}


def _escenario() -> tuple[RepositorioPerfilesFalso, AlmacenClaveValorFalso, TestClient]:
    repositorio = RepositorioPerfilesFalso()
    almacen = AlmacenClaveValorFalso()
    servicio = ServicioPerfiles(repositorio, BusEventosFalso(), CacheScore(almacen))
    return repositorio, almacen, TestClient(create_app(servicio_perfiles=servicio))


def test_crear_perfil_guarda_su_consentimiento() -> None:
    repositorio, _, cliente = _escenario()
    cuerpo = {
        "cliente_id": TITULAR,
        "fuentes": [],
        "consentimiento_id": "c-1",
        "consentimiento_vence_en": "2026-11-30T12:00:00+00:00",
    }

    with cliente:
        assert cliente.post("/perfiles", json=cuerpo).status_code == 200

    metadatos = repositorio.metadatos_guardados[0]
    assert metadatos["consentimiento_id"] == "c-1"
    assert metadatos["consentimiento_vence_en"] == datetime(2026, 11, 30, 12, tzinfo=UTC)


def test_revocar_borra_el_perfil_y_su_cache_y_es_idempotente() -> None:
    repositorio, almacen, cliente = _escenario()
    repositorio.borrables = [PERFIL]
    claves = (clave_score(TITULAR, "v1"), clave_oferta(TITULAR, 2, "v1"))
    for clave in claves:
        almacen.set(clave, "{}")

    with cliente:
        primera = cliente.post("/perfiles/consentimiento-revocado", json=REVOCACION)
        segunda = cliente.post("/perfiles/consentimiento-revocado", json=REVOCACION)

    assert (primera.status_code, segunda.status_code) == (204, 204)
    assert repositorio.pedidos_borrado == [(TITULAR, "c-1"), (TITULAR, "c-1")]
    assert not any(clave in almacen.valores for clave in claves)


def test_redis_caido_no_impide_borrar_el_perfil() -> None:
    class AlmacenCaido(AlmacenClaveValorFalso):
        def delete(self, *nombres: str) -> None:
            raise ConnectionError

    repositorio = RepositorioPerfilesFalso()
    repositorio.borrables = [PERFIL]
    servicio = ServicioPerfiles(repositorio, BusEventosFalso(), CacheScore(AlmacenCaido()))

    assert servicio.consentimiento_revocado(TITULAR, "c-1") == 1


def test_la_purga_cuenta_lo_borrado_y_limpia_la_cache() -> None:
    repositorio, almacen, cliente = _escenario()
    repositorio.borrables = [PERFIL]
    almacen.set(clave_score(TITULAR, "v1"), "{}")

    with cliente:
        respuesta = cliente.post("/perfiles/_purgar")

    assert respuesta.json() == {"perfiles_borrados": 1}
    assert clave_score(TITULAR, "v1") not in almacen.valores


def test_los_logs_no_llevan_el_titular(caplog: pytest.LogCaptureFixture) -> None:
    repositorio = RepositorioPerfilesFalso()
    repositorio.borrables = [PERFIL]
    servicio = ServicioPerfiles(repositorio, BusEventosFalso())

    with caplog.at_level(logging.INFO, logger="risk.perfiles"):
        servicio.consentimiento_revocado(TITULAR, "c-1")

    assert TITULAR not in caplog.text


class _ColeccionFalsa:
    def __init__(self) -> None:
        self.filtro: dict[str, Any] | None = None
        self.borrados: dict[str, Any] | None = None

    def find(self, filtro: dict[str, Any], _proyeccion: dict[str, int]) -> list[dict[str, Any]]:
        self.filtro = filtro
        return [{"_id": 1, **PERFIL}]

    def delete_many(self, filtro: dict[str, Any]) -> None:
        self.borrados = filtro


def test_revocar_uno_antiguo_no_alcanza_el_perfil_de_otro_consentimiento() -> None:
    coleccion = _ColeccionFalsa()
    repositorio = RepositorioPerfilesMongo.__new__(RepositorioPerfilesMongo)
    repositorio._perfiles = coleccion

    assert repositorio.borrar_de_consentimiento(TITULAR, "c-1")[0]["perfil_version"] == 2

    assert coleccion.filtro == {
        "cliente_id": TITULAR,
        "$or": [{"consentimiento_id": "c-1"}, {"consentimiento_id": None}],
    }
    assert coleccion.borrados == {"_id": {"$in": [1]}}
