"""HU-M06 (KAN-265, KAN-266): solicitar y cancelar la asistencia en sitio."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.application.asistencias import (
    SIMULACION_CONFIRMA,
    ErrorAsistencia,
    RepositorioAsistenciasMongo,
    ServicioAsistencias,
    distancia_km,
    minutos_estimados,
)
from app.application.cifrado import CIFRADO_CLAVE_LOCAL, CifradorAesGcm
from app.application.configuracion import Configuracion
from app.main import create_app

AHORA = datetime(2026, 11, 7, 15, tzinfo=UTC)
ID = "0b6f3a52-5d6e-4d8e-9c1a-2f4b7c9d1e00"
CLINICA = {
    "id": "prs-bog-001", "nombre": "Clínica Norte", "tipo": "MEDICO", "ciudad": "Bogotá",
    "latitud": 4.6859, "longitud": -74.0524, "activo": True,
}  # fmt: skip


class RepoAsistenciasFalso:
    def __init__(self):
        self.prestadores = {
            "prs-bog-001": dict(CLINICA),
            "prs-bog-009": {**CLINICA, "id": "prs-bog-009", "activo": False},
        }
        self.asistencias: dict[str, dict] = {}

    def prestador(self, prestador_id):
        return self.prestadores.get(prestador_id)

    def obtener(self, asistencia_id):
        doc = self.asistencias.get(asistencia_id)
        return dict(doc) if doc else None

    def crear(self, asistencia):
        self.asistencias[asistencia["id"]] = dict(asistencia)

    def actualizar(self, asistencia_id, cambios):
        self.asistencias[asistencia_id].update(cambios)


def _solicitud(**cambios) -> dict:
    return {
        "id_solicitud": ID,
        "cliente_id": "cli-1",
        "prestador_id": "prs-bog-001",
        "tipo_asistencia": "MEDICO",
        "descripcion": "Dolor en el pecho",
        "direccion": "Cra 45 # 22-18",
        "latitud": 4.6760,
        "longitud": -74.0480,
        **cambios,
    }


def _servicio(repo=None, simulacion="primero_falla"):
    return ServicioAsistencias(repo or RepoAsistenciasFalso(), simulacion, reloj=lambda: AHORA)


# ---------- Reglas ----------


def test_el_tiempo_estimado_usa_las_mismas_reglas_de_la_app():
    km = distancia_km(4.6760, -74.0480, 4.6859, -74.0524)
    assert round(km, 2) == 1.2
    assert minutos_estimados(km) == 6  # ceil(1.2 km a 20 km/h = 3,6 min) + 2


def test_el_primer_intento_no_se_confirma_y_el_reintento_si():
    repo = RepoAsistenciasFalso()
    servicio = _servicio(repo)

    primera = servicio.solicitar(_solicitud())
    assert primera["estado"] == "NO_CONFIRMADA"
    assert primera["motivo"] == "PRESTADOR_SIN_RESPUESTA"
    assert primera["minutos_estimados"] is None

    reintento = servicio.solicitar(_solicitud())
    assert reintento["id"] == ID
    assert reintento["estado"] == "CONFIRMADA"
    assert reintento["minutos_estimados"] == 6
    assert reintento["motivo"] is None
    assert len(repo.asistencias) == 1  # el reenvío no crea otra
    assert repo.asistencias[ID]["intentos"] == 2


def test_reenviar_una_confirmada_la_devuelve_sin_cambios():
    servicio = _servicio(simulacion=SIMULACION_CONFIRMA)
    primera = servicio.solicitar(_solicitud())
    assert primera["estado"] == "CONFIRMADA"
    assert servicio.solicitar(_solicitud(descripcion="otra cosa")) == primera


def test_la_respuesta_no_devuelve_la_ubicacion_ni_el_cliente():
    respuesta = _servicio().solicitar(_solicitud())
    assert set(respuesta) == {
        "id", "estado", "tipo_asistencia", "prestador", "minutos_estimados", "motivo",
        "solicitada_en", "cancelada_en",
    }  # fmt: skip
    assert respuesta["prestador"]["nombre"] == "Clínica Norte"


@pytest.mark.parametrize(
    ("cambios", "codigo"),
    [
        ({"prestador_id": "prs-no-existe"}, "PRESTADOR_NO_DISPONIBLE"),
        ({"prestador_id": "prs-bog-009"}, "PRESTADOR_NO_DISPONIBLE"),
        ({"tipo_asistencia": "MECANICO"}, "TIPO_NO_ATENDIDO"),
        ({"descripcion": "x" * 501}, "DATOS_INVALIDOS"),
        ({"direccion": "x" * 201}, "DATOS_INVALIDOS"),
    ],
)
def test_rechaza_con_un_codigo_estable(cambios, codigo):
    repo = RepoAsistenciasFalso()
    with pytest.raises(ErrorAsistencia) as error:
        _servicio(repo).solicitar(_solicitud(**cambios))
    assert error.value.codigo == codigo
    assert repo.asistencias == {}


def test_el_mismo_id_de_otro_cliente_no_se_le_entrega():
    servicio = _servicio()
    servicio.solicitar(_solicitud())
    with pytest.raises(ErrorAsistencia) as error:
        servicio.solicitar(_solicitud(cliente_id="cli-2"))
    assert error.value.codigo == "DATOS_INVALIDOS"


def test_cancelar_es_idempotente():
    servicio = _servicio(simulacion=SIMULACION_CONFIRMA)
    servicio.solicitar(_solicitud())
    cancelada = servicio.cancelar(ID, "cli-1")
    assert cancelada["estado"] == "CANCELADA"
    assert cancelada["cancelada_en"] == AHORA
    assert servicio.cancelar(ID, "cli-1") == cancelada
    # Reenviar la solicitud ya cancelada no la revive.
    assert servicio.solicitar(_solicitud())["estado"] == "CANCELADA"


@pytest.mark.parametrize(("aid", "cliente"), [(ID, "cli-2"), ("no-existe", "cli-1")])
def test_cancelar_una_ajena_o_inexistente_es_no_encontrada(aid, cliente):
    servicio = _servicio()
    servicio.solicitar(_solicitud())
    with pytest.raises(ErrorAsistencia) as error:
        servicio.cancelar(aid, cliente)
    assert error.value.codigo == "NO_ENCONTRADA"


def test_simulacion_desconocida_no_arranca():
    with pytest.raises(ValueError):
        ServicioAsistencias(RepoAsistenciasFalso(), "siempre")


# ---------- Repositorio Mongo: cifrado por campo ----------


class _Coleccion:
    def __init__(self, docs=None):
        self.docs = {d["_id"]: d for d in docs or []}

    def find_one(self, filtro):
        doc = self.docs.get(filtro["_id"])
        return dict(doc) if doc else None

    def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)

    def update_one(self, filtro, cambio):
        self.docs[filtro["_id"]].update(cambio["$set"])


class _Mongo:
    def __init__(self):
        prestador = {**{k: v for k, v in CLINICA.items() if k != "id"}, "_id": CLINICA["id"]}
        self.db = {"asistencias": _Coleccion(), "prestadores": _Coleccion([prestador])}

    def __getitem__(self, _nombre_db):
        return self.db


def test_el_repositorio_guarda_la_ubicacion_y_la_descripcion_cifradas():
    mongo = _Mongo()
    repo = RepositorioAsistenciasMongo(mongo, CifradorAesGcm.desde_base64(CIFRADO_CLAVE_LOCAL))
    _servicio(repo).solicitar(_solicitud())

    guardado = mongo.db["asistencias"].docs[ID]
    crudo = json.dumps(guardado, default=str)
    assert "Cra 45" not in crudo and "Dolor" not in crudo and "4.676" not in crudo
    assert "ubicacion" not in guardado and "descripcion" not in guardado

    leido = repo.obtener(ID)
    assert leido["ubicacion"] == {
        "latitud": 4.676, "longitud": -74.048, "direccion": "Cra 45 # 22-18"
    }  # fmt: skip
    assert leido["descripcion"] == "Dolor en el pecho"
    assert leido["prestador"]["nombre"] == "Clínica Norte"


# ---------- HTTP ----------


def _cliente(simulacion="primero_falla") -> TestClient:
    app = create_app(
        config=Configuracion(mongo_uri="mongodb://no-se-usa"),
        servicio=object(),  # la mesa de siniestros no se usa aquí
        reiniciar=lambda: 0,
        catalogo=object(),
        asistencias=_servicio(simulacion=simulacion),
    )
    return TestClient(app)


def test_http_solicitar_reintentar_y_cancelar():
    with _cliente() as http:
        r = http.post("/asistencias", json=_solicitud())
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "NO_CONFIRMADA"
        r = http.post("/asistencias", json=_solicitud())
        assert r.json()["estado"] == "CONFIRMADA"
        r = http.post(f"/asistencias/{ID}/cancelar", json={"cliente_id": "cli-1"})
        assert r.status_code == 200
        assert r.json()["estado"] == "CANCELADA"


def test_http_errores_de_negocio_con_codigo():
    with _cliente() as http:
        r = http.post("/asistencias", json=_solicitud(prestador_id="prs-bog-009"))
        assert r.status_code == 409
        assert r.json()["codigo"] == "PRESTADOR_NO_DISPONIBLE"
        r = http.post(f"/asistencias/{ID}/cancelar", json={"cliente_id": "cli-1"})
        assert r.status_code == 404
        assert r.json()["codigo"] == "NO_ENCONTRADA"
        r = http.post("/asistencias", json=_solicitud(tipo_asistencia="GRUA"))
        assert r.status_code == 422
