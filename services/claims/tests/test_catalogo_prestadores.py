"""HU-M06 (KAN-263): catálogo maestro de prestadores, sincronizado al migrar y versionado."""

import importlib
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from migrations import catalogos
from migrations.catalogos import ErrorCatalogo, huella, leer, sincronizar_prestadores

from app.application.catalogo_prestadores import CatalogoNoDisponible, consultar
from app.main import create_app

migracion = importlib.import_module("migrations.versiones.0005_prestadores")
migracion_detalle = importlib.import_module("migrations.versiones.0007_prestadores_detalle")
AHORA = datetime(2026, 11, 4, 12, tzinfo=UTC)


class Coleccion:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def find_one(self, filtro, proyeccion=None):
        return self.docs.get(filtro["_id"])

    def update_one(self, filtro, cambio, upsert=False):
        doc = self.docs.setdefault(filtro["_id"], {"_id": filtro["_id"]})
        doc.update(cambio["$set"])


class Db:
    def __init__(self):
        self.colecciones: dict[str, Coleccion] = {}

    def __getitem__(self, nombre):
        return self.colecciones.setdefault(nombre, Coleccion())


def _archivo(tmp_path, version="2026-11-04.1", **cambios):
    prestadores = [
        {"id": "prs-bog-001", "nombre": "Clínica Norte", "tipo": "MEDICO", "ciudad": "Bogotá",
         "latitud": 4.6859, "longitud": -74.0524, "activo": True, "aliado": True,
         "especialidad": "Urgencias", "direccion": "Cl. 93 # 14-20", "horario": "Abierto 24 h",
         "foto_url": None},
        {"id": "prs-bog-002", "nombre": "Taller AutoAyuda", "tipo": "MECANICO", "ciudad": "Bogotá",
         "latitud": 4.6835, "longitud": -74.0322, "activo": True, "aliado": False,
         "especialidad": "Mecánica general", "direccion": "Cra. 11 # 94-35", "horario": None,
         "foto_url": None},
    ]  # fmt: skip
    prestadores[0].update(cambios)
    ruta = tmp_path / "prestadores.json"
    ruta.write_text(json.dumps({"version": version, "prestadores": prestadores}), encoding="utf-8")
    return ruta


# ---------- Archivo del catálogo ----------


def test_el_catalogo_del_repo_es_valido():
    version, prestadores = leer()
    assert version == "2026-11-12.1"
    assert len(prestadores) == 17
    assert {p["tipo"] for p in prestadores} == {"MEDICO", "MECANICO", "GRUA"}
    # v0.49: hay aliados y no aliados para probar los dos colores en la app.
    assert {p["aliado"] for p in prestadores} == {True, False}


@pytest.mark.parametrize(
    ("cambio", "mensaje"),
    [
        ({"version": "4-nov"}, "versión inválida"),
        ({"tipo": "BOMBEROS"}, "tipo desconocido"),
    ],
)
def test_rechaza_un_archivo_invalido(tmp_path, cambio, mensaje):
    version = cambio.pop("version", "2026-11-04.1")
    with pytest.raises(ErrorCatalogo, match=mensaje):
        leer(_archivo(tmp_path, version, **cambio))


def test_rechaza_un_prestador_sin_el_detalle_de_la_v046(tmp_path):
    ruta = tmp_path / "prestadores.json"
    viejo = {"id": "prs-x", "nombre": "X", "tipo": "MEDICO", "ciudad": "Bogotá",
             "latitud": 4.6, "longitud": -74.0, "activo": True}  # fmt: skip
    ruta.write_text(json.dumps({"version": "2026-11-12.1", "prestadores": [viejo]}))
    with pytest.raises(ErrorCatalogo, match="aliado"):
        leer(ruta)


def test_rechaza_ids_repetidos(tmp_path):
    ruta = _archivo(tmp_path, id="prs-bog-002")
    with pytest.raises(ErrorCatalogo, match="repetido"):
        leer(ruta)


def test_la_huella_no_depende_del_orden():
    _, prestadores = leer()
    assert huella(prestadores) == huella(list(reversed(prestadores)))


# ---------- Sincronización (corre en cada `python -m migrations`) ----------


def test_siembra_y_una_segunda_pasada_no_cambia_nada(tmp_path):
    db, ruta = Db(), _archivo(tmp_path)
    assert "2 sincronizados" in sincronizar_prestadores(db, ruta, AHORA)
    antes = json.dumps(db["prestadores"].docs, sort_keys=True, default=str)
    assert sincronizar_prestadores(db, ruta, AHORA).endswith("al día")
    assert json.dumps(db["prestadores"].docs, sort_keys=True, default=str) == antes
    assert db["catalogos"].docs["prestadores"]["version"] == "2026-11-04.1"


def test_cambiar_el_contenido_sin_subir_la_version_falla(tmp_path):
    db = Db()
    sincronizar_prestadores(db, _archivo(tmp_path), AHORA)
    with pytest.raises(ErrorCatalogo, match="sube `version`"):
        sincronizar_prestadores(db, _archivo(tmp_path, nombre="Clínica Norte 2"), AHORA)


def test_dar_de_baja_es_activo_false_y_nunca_borra(tmp_path):
    db = Db()
    sincronizar_prestadores(db, _archivo(tmp_path), AHORA)
    db["prestadores"].docs["prs-viejo"] = {"_id": "prs-viejo", "activo": True}
    sincronizar_prestadores(db, _archivo(tmp_path, "2026-11-05.1", activo=False), AHORA)
    assert db["prestadores"].docs["prs-bog-001"]["activo"] is False
    assert "prs-viejo" in db["prestadores"].docs
    assert db["catalogos"].docs["prestadores"]["version"] == "2026-11-05.1"


def test_no_baja_a_una_version_anterior(tmp_path):
    db = Db()
    sincronizar_prestadores(db, _archivo(tmp_path, "2026-11-05.10"), AHORA)
    resultado = sincronizar_prestadores(db, _archivo(tmp_path, "2026-11-05.9", nombre="X"), AHORA)
    assert "más nueva" in resultado
    assert db["prestadores"].docs["prs-bog-001"]["nombre"] == "Clínica Norte"


def test_la_migracion_crea_las_dos_colecciones_con_validacion():
    class DbMigracion:
        def __init__(self):
            self.creadas = {}

        def list_collection_names(self):
            return []

        def create_collection(self, nombre, validator, validationLevel):
            self.creadas[nombre] = validator

    db = DbMigracion()
    migracion.aplicar(db)
    assert set(db.creadas) == {"prestadores", "catalogos"}
    assert set(db.creadas["prestadores"]["$jsonSchema"]["properties"]["tipo"]["enum"]) == {
        "MEDICO",
        "MECANICO",
    }


def test_la_0007_agrega_grua_y_el_detalle_sin_volverlos_obligatorios():
    class DbMigracion:
        def __init__(self):
            self.comandos = []

        def list_collection_names(self):
            return ["prestadores"]

        def command(self, *args, **kwargs):
            self.comandos.append((args, kwargs))

    db = DbMigracion()
    migracion_detalle.aplicar(db)
    ((args, kwargs),) = db.comandos
    assert args == ("collMod", "prestadores")
    esquema = kwargs["validator"]["$jsonSchema"]
    assert set(esquema["properties"]["tipo"]["enum"]) == catalogos.TIPOS
    assert set(catalogos.CAMPOS_DETALLE) <= set(esquema["properties"])
    assert not set(catalogos.CAMPOS_DETALLE) & set(esquema["required"])


# ---------- Consulta versionada ----------


class LectorFalso:
    def __init__(self, version="2026-11-04.1"):
        self.version = version

    def version_vigente(self):
        if self.version is None:
            raise CatalogoNoDisponible
        return self.version

    def prestadores(self):
        return [{"id": "prs-bog-001", "activo": True}, {"id": "prs-bog-002", "activo": False}]


@pytest.mark.parametrize("version_local", [None, "2026-10-01.1"])
def test_sin_version_o_con_una_vieja_trae_la_lista_completa(version_local):
    r = consultar(LectorFalso(), version_local)
    assert r["vigente"] is False
    assert r["version"] == "2026-11-04.1"
    assert [p["id"] for p in r["prestadores"]] == ["prs-bog-001", "prs-bog-002"]


def test_con_la_version_vigente_viaja_vacia():
    assert consultar(LectorFalso(), "2026-11-04.1") == {
        "version": "2026-11-04.1",
        "vigente": True,
        "prestadores": [],
    }


def test_endpoint_y_503_sin_catalogo():
    from fakes import repositorio_con_datos

    from app.application.servicio_siniestros import ServicioSiniestros

    servicio = ServicioSiniestros(repositorio_con_datos())
    with TestClient(create_app(servicio=servicio, catalogo=LectorFalso())) as c:
        r = c.get("/prestadores", params={"version_local": "2026-11-04.1"})
        assert r.status_code == 200
        assert r.json()["vigente"] is True
        assert len(c.get("/prestadores").json()["prestadores"]) == 2
    with TestClient(create_app(servicio=servicio, catalogo=LectorFalso(None))) as c:
        r = c.get("/prestadores")
        assert r.status_code == 503
        assert "no está disponible" in r.json()["detail"]
