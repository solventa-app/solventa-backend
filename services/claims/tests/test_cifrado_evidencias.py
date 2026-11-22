"""HA-SEG-002: la ubicación de las fotos del reporte (HU-M05) se guarda cifrada en MongoDB."""

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.application.cifrado import CIFRADO_CLAVE_LOCAL, CifradorAesGcm
from app.application.repositorio import EVIDENCIAS, RepositorioSiniestrosMongo
from app.main import Configuracion, create_app

AHORA = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


def _evidencia(eid="sin-1-foto-1", **cambios) -> dict:
    return {
        "id": eid,
        "siniestro_id": "sin-1",
        "archivo_id": "arch-1",
        "tipo": "FOTO",
        "titulo": "Foto 1 del daño (app)",
        "nombre_archivo": f"{eid}.jpg",
        "registrada_en": AHORA,
        "geoetiquetada": True,
        "latitud": 4.6767,
        "longitud": -74.0483,
        "nota": "4.6767, -74.0483 · tomada en la app",
        "creado_en": AHORA,
        **cambios,
    }


class _Coleccion:
    """Lo mínimo de pymongo que usan las evidencias: buscar por igualdad, `$exists` y `$or`."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    @staticmethod
    def _cumple(doc, filtro) -> bool:
        for campo, condicion in filtro.items():
            if campo == "$or":
                if not any(_Coleccion._cumple(doc, f) for f in condicion):
                    return False
            elif isinstance(condicion, dict) and "$exists" in condicion:
                if (campo in doc) != condicion["$exists"]:
                    return False
            elif doc.get(campo) != condicion:
                return False
        return True

    def find_one(self, filtro):
        return next((dict(d) for d in self.docs.values() if self._cumple(d, filtro)), None)

    def find(self, filtro):
        return [dict(d) for d in self.docs.values() if self._cumple(d, filtro)]

    def replace_one(self, filtro, doc, upsert=False):
        self.docs[filtro["_id"]] = dict(doc)

    def update_one(self, filtro, cambio):
        doc = next((d for d in self.docs.values() if self._cumple(d, filtro)), None)

        class _Resultado:
            modified_count = 0

        if doc is None:
            return _Resultado()
        doc.update(cambio.get("$set", {}))
        for campo in cambio.get("$unset", {}):
            doc.pop(campo, None)
        _Resultado.modified_count = 1
        return _Resultado()


class _Mongo:
    def __init__(self):
        self.db = {EVIDENCIAS: _Coleccion()}

    def __getitem__(self, _nombre_db):
        return self.db


def _repo():
    mongo = _Mongo()
    repo = RepositorioSiniestrosMongo(mongo, CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL))
    return repo, mongo.db[EVIDENCIAS]


def test_la_ubicacion_de_la_foto_se_guarda_cifrada_y_se_lee_en_claro():
    repo, coleccion = _repo()
    repo.guardar_evidencia(_evidencia())

    guardado = coleccion.docs["sin-1-foto-1"]
    crudo = json.dumps(guardado, default=str)
    assert "4.67" not in crudo and "-74.04" not in crudo
    assert {"latitud", "longitud", "nota"}.isdisjoint(guardado)
    assert guardado["geoetiquetada"] is True  # no es un dato personal

    leido = repo.evidencia("sin-1-foto-1")
    assert leido["latitud"] == 4.6767 and leido["longitud"] == -74.0483
    assert leido["nota"] == "4.6767, -74.0483 · tomada en la app"
    assert "ubicacion_cifrada" not in leido


def test_una_foto_sin_ubicacion_tambien_queda_cifrada():
    repo, _ = _repo()
    repo.guardar_evidencia(
        _evidencia(geoetiquetada=False, latitud=None, longitud=None, nota="Sin ubicación")
    )
    leido = repo.evidencia("sin-1-foto-1")
    assert leido["latitud"] is None and leido["nota"] == "Sin ubicación"


def test_el_cifrado_esta_atado_a_la_evidencia():
    """Copiar el campo cifrado a otra evidencia no se descifra (el id es el dato asociado)."""
    repo, coleccion = _repo()
    repo.guardar_evidencia(_evidencia())
    repo.guardar_evidencia(_evidencia("sin-1-foto-2", latitud=1.0, longitud=2.0, nota="x"))
    coleccion.docs["sin-1-foto-2"]["ubicacion_cifrada"] = coleccion.docs["sin-1-foto-1"][
        "ubicacion_cifrada"
    ]
    try:
        repo.evidencia("sin-1-foto-2")
    except Exception:  # noqa: BLE001 - AES-GCM rechaza el dato asociado distinto
        return
    raise AssertionError("se descifró con el id de otra evidencia")


def test_las_evidencias_legadas_se_leen_y_se_cifran_una_sola_vez():
    repo, coleccion = _repo()
    legada = {k: v for k, v in _evidencia().items() if k != "id"}
    coleccion.docs["sin-1-foto-1"] = {**legada, "_id": "sin-1-foto-1"}
    repo.guardar_evidencia(_evidencia("sin-1-foto-2"))

    # Antes de migrar se lee igual.
    assert repo.evidencia("sin-1-foto-1")["latitud"] == 4.6767

    assert repo.cifrar_evidencias_legadas() == 1
    assert repo.cifrar_evidencias_legadas() == 0  # idempotente

    guardado = coleccion.docs["sin-1-foto-1"]
    assert {"latitud", "longitud", "nota"}.isdisjoint(guardado)
    leido = repo.evidencia("sin-1-foto-1")
    assert leido["latitud"] == 4.6767 and leido["nota"] == "4.6767, -74.0483 · tomada en la app"


def test_http_cifrar_legadas():
    class Servicio:
        def cifrar_evidencias_legadas(self):
            return {"cifradas": 3}

    app = create_app(config=Configuracion(mongo_uri="mongodb://no-se-usa"), servicio=Servicio())
    with TestClient(app) as http:
        r = http.post("/evidencias/_cifrar-legadas")
    assert r.status_code == 200 and r.json() == {"cifradas": 3}
