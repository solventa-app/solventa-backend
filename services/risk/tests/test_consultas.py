"""Historial de consultas de fuentes (QA 2026-11-10, Q-08, D-2): se guarda por solicitud, solo el
asesor que la hizo la ve, y nunca lleva los datos de las fuentes. Sin MongoDB real: una colección en
memoria con la parte de la API de pymongo que usa el repositorio."""

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

from fakes import BusEventosFalso, RepositorioPerfilesFalso
from fastapi.testclient import TestClient

from app.application.consultas import (
    COLECCION,
    TAMANO_MAXIMO,
    RepositorioConsultasMongo,
    ServicioConsultas,
)
from app.application.repositorio_perfiles import NOMBRE_BASE
from app.application.servicio_perfiles import ServicioPerfiles
from app.main import create_app


class CursorFalso:
    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        self._documentos = documentos

    def sort(self, criterio: list[tuple[str, int]]) -> "CursorFalso":
        for campo, sentido in reversed(criterio):
            self._documentos.sort(key=lambda d, c=campo: d[c], reverse=sentido < 0)
        return self

    def skip(self, n: int) -> "CursorFalso":
        self._documentos = self._documentos[n:]
        return self

    def limit(self, n: int) -> "CursorFalso":
        self._documentos = self._documentos[:n]
        return self

    def __iter__(self):
        return iter(self._documentos)


class ColeccionFalsa:
    def __init__(self) -> None:
        self.documentos: list[dict[str, Any]] = []
        self.indices: list[list[tuple[str, int]]] = []

    @staticmethod
    def _casa(doc: dict[str, Any], filtro: dict[str, Any]) -> bool:
        return all(doc.get(k) == v for k, v in filtro.items())

    def insert_one(self, documento: dict[str, Any]) -> None:
        self.documentos.append(dict(documento))

    def count_documents(self, filtro: dict[str, Any]) -> int:
        return sum(1 for d in self.documentos if self._casa(d, filtro))

    def find(self, filtro: dict[str, Any]) -> CursorFalso:
        return CursorFalso([dict(d) for d in self.documentos if self._casa(d, filtro)])

    def find_one(self, filtro: dict[str, Any]) -> dict[str, Any] | None:
        return next((dict(d) for d in self.documentos if self._casa(d, filtro)), None)

    def create_index(self, claves: list[tuple[str, int]]) -> None:
        self.indices.append(claves)


class MongoFalso:
    """`cliente[NOMBRE_BASE][COLECCION]` como lo usa el repositorio."""

    def __init__(self) -> None:
        self.coleccion = ColeccionFalsa()

    def __getitem__(self, nombre: str) -> dict[str, ColeccionFalsa]:
        assert nombre == NOMBRE_BASE
        return {COLECCION: self.coleccion}


FUENTES = [
    {
        "id": "historial-crediticio",
        "nombre": "historial-crediticio",
        "origen": "OPEN_FINANCE",
        "estado": "OK",
        "capturado_en": None,
        # Lo que NUNCA debe quedar guardado:
        "datos": {"score_crediticio": 780},
    },
    {
        "id": "afiliacion-pila",
        "nombre": "afiliacion-pila",
        "origen": "OPEN_DATA",
        "estado": "CACHE",
        "capturado_en": "2026-11-09T10:00:00+00:00",
    },
]


def servicio() -> tuple[ServicioConsultas, MongoFalso]:
    mongo = MongoFalso()
    return ServicioConsultas(RepositorioConsultasMongo(mongo)), mongo  # type: ignore[arg-type]


def registrar(s: ServicioConsultas, actor: str = "asesor-1", **extra: Any) -> str:
    return s.registrar(
        actor,
        "ASESOR",
        "titular-hmac",
        "10••••3040",
        "cons-1",
        "COMPLETA",
        "VIDA_DEUDOR",
        FUENTES,
        **extra,
    )


def test_guarda_el_estado_de_cada_fuente_y_nunca_sus_datos() -> None:
    s, mongo = servicio()
    consulta_id = registrar(s, oferta_id="of-1")

    crudo = mongo.coleccion.documentos[0]
    assert crudo["_id"] == consulta_id
    assert crudo["actor_id"] == "asesor-1"
    assert crudo["documento_enmascarado"] == "10••••3040"
    assert crudo["oferta_id"] == "of-1"
    assert [f["id"] for f in crudo["fuentes"]] == ["historial-crediticio", "afiliacion-pila"]
    assert all("datos" not in f for f in crudo["fuentes"])
    assert "780" not in str(crudo)

    detalle = s.obtener(consulta_id, "asesor-1")
    assert detalle is not None
    assert detalle["tipo_oferta"] == "COMPLETA"
    assert detalle["fuentes"][1] == {
        "id": "afiliacion-pila",
        "nombre": "afiliacion-pila",
        "origen": "OPEN_DATA",
        "estado": "CACHE",
        "capturado_en": "2026-11-09T10:00:00+00:00",
    }


def test_lista_de_la_mas_reciente_a_la_mas_antigua_y_pagina() -> None:
    s, _ = servicio()
    base = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    ids = [registrar(s, creada_en=base + timedelta(minutes=i)) for i in range(25)]

    primera = s.listar("asesor-1", 1, 10)
    assert primera["total"] == 25 and primera["pagina"] == 1 and primera["tamano"] == 10
    assert [c["id"] for c in primera["consultas"]] == list(reversed(ids))[:10]

    tercera = s.listar("asesor-1", 3, 10)
    assert len(tercera["consultas"]) == 5
    assert tercera["consultas"][-1]["id"] == ids[0]


def test_cada_asesor_ve_solo_las_suyas() -> None:
    s, _ = servicio()
    mia = registrar(s, "asesor-1")
    ajena = registrar(s, "asesor-2")

    assert [c["id"] for c in s.listar("asesor-1", 1, 10)["consultas"]] == [mia]
    assert s.listar("asesor-3", 1, 10) == {"consultas": [], "total": 0, "pagina": 1, "tamano": 10}
    # Una ajena responde igual que una inexistente.
    assert s.obtener(ajena, "asesor-1") is None
    assert s.obtener("no-existe", "asesor-1") is None


def test_el_tamano_de_pagina_tiene_techo() -> None:
    s, _ = servicio()
    assert s.listar("asesor-1", 1, 10_000)["tamano"] == TAMANO_MAXIMO
    assert s.listar("asesor-1", 0, 0)["pagina"] == 1


def test_http_registrar_listar_y_obtener() -> None:
    s, _ = servicio()
    cuerpo = {
        "actor_id": "asesor-1",
        "actor_rol": "ASESOR",
        "titular_id": "titular-hmac",
        "documento_enmascarado": "10••••3040",
        "consentimiento_id": "cons-1",
        "tipo_oferta": "PRELIMINAR",
        "producto": "VIDA_DEUDOR",
        "oferta_id": "of-1",
        "fuentes": [{k: v for k, v in f.items() if k != "datos"} for f in FUENTES],
    }
    with TestClient(create_app(servicio_perfiles=_perfiles(), servicio_consultas=s)) as client:
        creada = client.post("/consultas", json=cuerpo)
        assert creada.status_code == 200
        consulta_id = creada.json()["id"]

        lista = client.get("/consultas", params={"actor_id": "asesor-1"}).json()
        assert lista["total"] == 1 and lista["consultas"][0]["id"] == consulta_id
        assert lista["consultas"][0]["tipo_oferta"] == "PRELIMINAR"

        detalle = client.get(f"/consultas/{consulta_id}", params={"actor_id": "asesor-1"})
        assert detalle.status_code == 200
        assert detalle.json()["fuentes"][0]["estado"] == "OK"

        ajeno = client.get(f"/consultas/{consulta_id}", params={"actor_id": "asesor-2"})
        assert ajeno.status_code == 404
        assert client.get("/consultas", params={"actor_id": "asesor-2"}).json()["total"] == 0


def test_http_valida_la_paginacion() -> None:
    s, _ = servicio()
    with TestClient(create_app(servicio_perfiles=_perfiles(), servicio_consultas=s)) as client:
        assert client.get("/consultas", params={"actor_id": "a", "pagina": 0}).status_code == 422
        assert client.get("/consultas", params={"actor_id": "a", "tamano": 51}).status_code == 422
        assert client.get("/consultas").status_code == 422


def test_revocar_el_consentimiento_borra_el_perfil_pero_no_la_consulta() -> None:
    """La consulta es rastro de lo que se consultó: no lleva datos y se conserva (D-2)."""
    s, _ = servicio()
    registrar(s)
    with TestClient(create_app(servicio_perfiles=_perfiles(), servicio_consultas=s)) as client:
        borrado = client.post(
            "/perfiles/consentimiento-revocado",
            json={"titular_id": "titular-hmac", "consentimiento_id": "cons-1"},
        )
        assert borrado.status_code == 204
        assert client.get("/consultas", params={"actor_id": "asesor-1"}).json()["total"] == 1


def test_sin_servicio_de_consultas_responde_503() -> None:
    with TestClient(create_app(servicio_perfiles=_perfiles())) as client:
        assert client.get("/consultas", params={"actor_id": "a"}).status_code == 503


def test_la_migracion_crea_el_indice_por_actor_y_fecha_y_es_idempotente() -> None:
    migracion = importlib.import_module("migrations.versiones.0006_consultas_fuentes")
    coleccion = ColeccionFalsa()
    db = {"consultas": coleccion}

    migracion.aplicar(db)
    migracion.aplicar(db)

    assert coleccion.indices == [[("actor_id", 1), ("creada_en", -1)]] * 2


def _perfiles() -> ServicioPerfiles:
    return ServicioPerfiles(RepositorioPerfilesFalso(), BusEventosFalso())
