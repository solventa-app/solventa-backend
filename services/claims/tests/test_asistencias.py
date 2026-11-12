"""HU-M06 (KAN-265, KAN-266): solicitar y cancelar la asistencia en sitio."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.application.asistencias import (
    ErrorAsistencia,
    RepositorioAsistenciasMongo,
    ServicioAsistencias,
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

    def de_cliente(self, cliente_id, desde):
        return sorted(
            (
                dict(a)
                for a in self.asistencias.values()
                if a["cliente_id"] == cliente_id and a["solicitada_en"] >= desde
            ),
            key=lambda a: a["solicitada_en"],
            reverse=True,
        )


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


class RedFalsa:
    """Hace de ACL + `stubs/prestadores`: `primero_falla` no confirma el primer intento; con
    coordenadas confirma en 6 min, sin ellas sin tiempo. `caida` lanza como un ACL caído."""

    def __init__(self, simulacion="primero_falla"):
        self.simulacion = simulacion
        self.pedidos: list[dict] = []

    def solicitar(self, pedido):
        self.pedidos.append(pedido)
        if self.simulacion == "caida":
            raise ConnectionError("ACL no responde")
        if self.simulacion == "primero_falla" and pedido["intento"] == 1:
            return {"estado": "NO_CONFIRMADA", "motivo": "PRESTADOR_SIN_RESPUESTA"}
        minutos = 6 if pedido["latitud"] is not None else None
        return {"estado": "CONFIRMADA", "minutos_estimados": minutos, "motivo": None}


def _servicio(repo=None, simulacion="primero_falla", red=None):
    return ServicioAsistencias(
        repo or RepoAsistenciasFalso(), red or RedFalsa(simulacion), reloj=lambda: AHORA
    )


# ---------- Reglas ----------


def test_el_desenlace_lo_decide_la_red_por_el_acl_sin_el_cliente_ni_la_descripcion():
    red = RedFalsa("confirma")
    _servicio(red=red).solicitar(_solicitud())
    (pedido,) = red.pedidos
    assert pedido == {
        "asistencia_id": ID, "intento": 1, "tipo_asistencia": "MEDICO",
        "prestador_id": "prs-bog-001", "prestador_latitud": 4.6859, "prestador_longitud": -74.0524,
        "latitud": 4.6760, "longitud": -74.0480,
    }  # fmt: skip


def test_si_el_acl_no_responde_queda_no_confirmada_y_se_puede_reintentar():
    repo = RepoAsistenciasFalso()
    caida = _servicio(repo, red=RedFalsa("caida")).solicitar(_solicitud())
    assert caida["estado"] == "NO_CONFIRMADA"
    assert caida["motivo"] == "PRESTADOR_SIN_RESPUESTA"
    reintento = _servicio(repo, simulacion="confirma").solicitar(_solicitud())
    assert reintento["estado"] == "CONFIRMADA"
    assert repo.asistencias[ID]["intentos"] == 2


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


def _sin_ubicacion(**cambios) -> dict:
    return {k: v for k, v in _solicitud(**cambios).items() if k not in ("latitud", "longitud")}


def test_con_la_direccion_a_mano_se_confirma_sin_tiempo_estimado():
    repo = RepoAsistenciasFalso()
    servicio = _servicio(repo)

    primera = servicio.solicitar(_sin_ubicacion())
    assert primera["estado"] == "NO_CONFIRMADA"

    reintento = servicio.solicitar(_sin_ubicacion())
    assert reintento["estado"] == "CONFIRMADA"
    assert reintento["minutos_estimados"] is None
    assert repo.asistencias[ID]["ubicacion"] == {
        "latitud": None, "longitud": None, "direccion": "Cra 45 # 22-18",
        "origen": "DIRECCION",
    }  # fmt: skip


@pytest.mark.parametrize("direccion", [None, "", "   "])
def test_sin_ubicacion_la_direccion_es_obligatoria(direccion):
    repo = RepoAsistenciasFalso()
    with pytest.raises(ErrorAsistencia) as error:
        _servicio(repo).solicitar(_sin_ubicacion(direccion=direccion))
    assert error.value.codigo == "DATOS_INVALIDOS"
    assert repo.asistencias == {}


def test_reenviar_una_confirmada_la_devuelve_sin_cambios():
    servicio = _servicio(simulacion="confirma")
    primera = servicio.solicitar(_solicitud())
    assert primera["estado"] == "CONFIRMADA"
    assert servicio.solicitar(_solicitud(descripcion="otra cosa")) == primera


def test_la_respuesta_no_devuelve_la_ubicacion_ni_el_cliente():
    respuesta = _servicio().solicitar(_solicitud())
    assert set(respuesta) == {
        "id", "estado", "tipo_asistencia", "prestador", "minutos_estimados", "motivo",
        "solicitada_en", "cancelada_en", "confirmada_en", "llegada_estimada",
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
    servicio = _servicio(simulacion="confirma")
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
        "latitud": 4.676, "longitud": -74.048, "direccion": "Cra 45 # 22-18", "origen": "GPS",
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
        assert r.status_code == 409  # v0.49: GRUA existe, pero esta clínica no la atiende
        assert r.json()["codigo"] == "TIPO_NO_ATENDIDO"
        r = http.post("/asistencias", json=_solicitud(tipo_asistencia="BOMBEROS"))
        assert r.status_code == 422


def test_v050_con_la_direccion_ubicada_hay_coordenadas_y_origen_direccion():
    repo = RepoAsistenciasFalso()
    red = RedFalsa("confirma")
    r = _servicio(repo, red=red).solicitar(_solicitud(origen_ubicacion="DIRECCION"))
    assert r["minutos_estimados"] == 6  # con coordenadas, hay tiempo de llegada
    assert repo.asistencias[ID]["ubicacion"]["origen"] == "DIRECCION"
    assert red.pedidos[0]["latitud"] == 4.6760


def test_v050_con_origen_direccion_la_direccion_es_obligatoria():
    with pytest.raises(ErrorAsistencia) as error:
        _servicio().solicitar(_solicitud(origen_ubicacion="DIRECCION", direccion=""))
    assert error.value.codigo == "DATOS_INVALIDOS"


def test_con_gps_el_origen_es_gps_por_defecto():
    repo = RepoAsistenciasFalso()
    _servicio(repo).solicitar(_solicitud())
    assert repo.asistencias[ID]["ubicacion"]["origen"] == "GPS"


# ---------- v0.52 (B5 y B6): «Mis asistencias» ----------


def _con_reloj(repo, red, momentos):
    it = iter(momentos)
    return ServicioAsistencias(repo, red, reloj=lambda: next(it))


def test_v052_al_confirmar_queda_la_llegada_y_al_pasar_se_lee_como_llego():
    from datetime import timedelta

    repo = RepoAsistenciasFalso()
    momentos = [AHORA, AHORA, AHORA + timedelta(minutes=3), AHORA + timedelta(minutes=7)]
    s = _con_reloj(repo, RedFalsa("confirma"), momentos)
    r = s.solicitar(_solicitud())
    assert r["estado"] == "CONFIRMADA"
    assert r["confirmada_en"] == AHORA
    assert r["llegada_estimada"] == AHORA + timedelta(minutes=6)
    # A los 3 minutos sigue en camino; a los 7 ya llegó (derivado al leer, no guardado).
    assert s.detalle(ID, "cli-1")["estado"] == "CONFIRMADA"
    assert s.detalle(ID, "cli-1")["estado"] == "LLEGO"
    assert repo.asistencias[ID]["estado"] == "CONFIRMADA"


def test_v052_mis_asistencias_del_cliente_con_lo_que_pidio_y_adonde():
    from datetime import timedelta

    repo = RepoAsistenciasFalso()
    _servicio(repo, simulacion="confirma").solicitar(_solicitud())
    vieja = {**repo.asistencias[ID], "id": "vieja", "solicitada_en": AHORA - timedelta(days=31)}
    repo.asistencias["vieja"] = vieja
    otra = {**repo.asistencias[ID], "id": "otra", "cliente_id": "cli-2"}
    repo.asistencias["otra"] = otra
    (a,) = _servicio(repo).mis("cli-1")  # ni la de hace 31 días ni la de otro cliente
    assert a["id"] == ID
    assert a["descripcion"] == "Dolor en el pecho"
    assert a["ubicacion"] == {
        "latitud": 4.6760, "longitud": -74.0480, "direccion": "Cra 45 # 22-18", "origen": "GPS",
    }  # fmt: skip


def test_v052_el_detalle_de_otro_cliente_no_existe():
    repo = RepoAsistenciasFalso()
    _servicio(repo).solicitar(_solicitud())
    with pytest.raises(ErrorAsistencia) as error:
        _servicio(repo).detalle(ID, "cli-2")
    assert error.value.codigo == "NO_ENCONTRADA"


def test_v052_http_mis_y_detalle():
    with _cliente("confirma") as http:
        http.post("/asistencias", json=_solicitud())
        r = http.get("/clientes/cli-1/asistencias")
        assert r.status_code == 200 and r.json()[0]["id"] == ID
        assert r.json()[0]["llegada_estimada"]
        assert http.get(f"/asistencias/{ID}", params={"cliente_id": "cli-1"}).status_code == 200
        r = http.get(f"/asistencias/{ID}", params={"cliente_id": "otro"})
        assert r.status_code == 404 and r.json()["codigo"] == "NO_ENCONTRADA"
