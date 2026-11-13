"""HU-M06 (v0.51, B8): rutas de los prestadores y su auditoría cifrada."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.application.cifrado import CIFRADO_CLAVE_LOCAL, CifradorAesGcm
from app.application.configuracion import Configuracion
from app.application.rutas import RepositorioAuditoriaRutas, ServicioRutas
from app.main import create_app

AHORA = datetime(2026, 11, 12, 15, tzinfo=UTC)
PRESTADORES = {
    "prs-1": {"latitud": 4.6859, "longitud": -74.0524, "activo": True},
    "prs-2": {"latitud": 4.6835, "longitud": -74.0322, "activo": True},
    "prs-3": {"latitud": 4.6536, "longitud": -74.0555, "activo": True},
    "prs-4": {"latitud": 4.6990, "longitud": -74.0350, "activo": True},
    "baja": {"latitud": 4.7, "longitud": -74.0, "activo": False},
}


class AclFalso:
    def __init__(self, caido=False, sin_direccion=False):
        self.pedidos = []
        self.caido = caido
        self.sin_direccion = sin_direccion

    def rutas(self, pedido):
        self.pedidos.append(pedido)
        if self.caido:
            raise ConnectionError("ACL caído")
        return {
            "proveedor": "simulado",
            "resultado": "OK",
            "rutas": [
                {"prestador_id": d["id"], "distancia_metros": 1000, "duracion_segundos": 360,
                 "geometria": "abc" if d["id"] in pedido["con_geometria"] else None,
                 "estimado": True}
                for d in pedido["destinos"]
            ],
        }  # fmt: skip

    def direccion_aproximada(self, latitud, longitud):
        if self.sin_direccion:
            raise ConnectionError("Google caído")
        return "Cl. 93 # 14, Bogotá"


class AuditoriaFalsa:
    def __init__(self, falla=False):
        self.registros = []
        self.falla = falla

    def registrar(self, registro):
        if self.falla:
            raise RuntimeError("Mongo caído")
        self.registros.append(registro)


def _servicio(acl=None, auditoria=None):
    return ServicioRutas(
        PRESTADORES.get,
        acl or AclFalso(),
        auditoria or AuditoriaFalsa(),
        reloj=lambda: AHORA,
        medir=iter([1.0, 1.25]).__next__,
    )


def test_tiempos_de_todos_y_geometria_de_los_3_destacados():
    acl = AclFalso()
    rutas = _servicio(acl).consultar(
        "cli-1", 4.6767, -74.0483, ["prs-1", "prs-2", "prs-3", "prs-4"]
    )
    assert [r["prestador_id"] for r in rutas] == ["prs-1", "prs-2", "prs-3", "prs-4"]
    (pedido,) = acl.pedidos
    assert pedido["con_geometria"] == ["prs-1", "prs-2", "prs-3"]
    assert pedido["destinos"][0] == {"id": "prs-1", "latitud": 4.6859, "longitud": -74.0524}


def test_ignora_los_dados_de_baja_los_desconocidos_y_los_repetidos():
    acl = AclFalso()
    _servicio(acl).consultar("cli-1", 4.6, -74.0, ["prs-1", "prs-1", "baja", "nada"])
    assert [d["id"] for d in acl.pedidos[0]["destinos"]] == ["prs-1"]
    assert _servicio().consultar("cli-1", 4.6, -74.0, ["nada"]) == []


def test_audita_con_proveedor_resultado_latencia_y_direccion():
    auditoria = AuditoriaFalsa()
    _servicio(auditoria=auditoria).consultar("cli-1", 4.6767, -74.0483, ["prs-1"])
    (r,) = auditoria.registros
    assert r["cliente_id"] == "cli-1" and r["prestador_ids"] == ["prs-1"]
    assert (r["proveedor"], r["resultado"], r["latencia_ms"]) == ("simulado", "OK", 250)
    assert r["ubicacion"] == {"latitud": 4.6767, "longitud": -74.0483}
    assert r["direccion_aproximada"] == "Cl. 93 # 14, Bogotá"
    assert r["creada_en"] == AHORA


def test_si_el_acl_cae_responde_vacio_y_audita_falla():
    auditoria = AuditoriaFalsa()
    s = _servicio(AclFalso(caido=True, sin_direccion=True), auditoria)
    assert s.consultar("cli-1", 4.6, -74.0, ["prs-1"]) == []
    assert auditoria.registros[0]["resultado"] == "FALLA"
    assert auditoria.registros[0]["direccion_aproximada"] is None


def test_auditar_nunca_rompe_la_respuesta():
    rutas = _servicio(auditoria=AuditoriaFalsa(falla=True)).consultar(
        "cli-1", 4.6, -74.0, ["prs-1"]
    )
    assert len(rutas) == 1


def test_el_repositorio_cifra_la_ubicacion_y_la_direccion():
    class Coleccion:
        def __init__(self):
            self.docs = []

        def insert_one(self, doc):
            self.docs.append(doc)

    coleccion = Coleccion()
    repo = RepositorioAuditoriaRutas({"claims": {"auditoria_rutas": coleccion}}, None)
    cifrador = CifradorAesGcm.desde_entorno(cifrado_clave=CIFRADO_CLAVE_LOCAL)
    repo._cifrador = cifrador
    repo.registrar(
        {"id": "r-1", "creada_en": AHORA, "cliente_id": "cli-1", "prestador_ids": ["prs-1"],
         "proveedor": "mapbox", "resultado": "OK", "latencia_ms": 120,
         "ubicacion": {"latitud": 4.6767, "longitud": -74.0483},
         "direccion_aproximada": "Cl. 93 # 14, Bogotá"}
    )  # fmt: skip
    (doc,) = coleccion.docs
    crudo = str(doc)
    assert "4.6767" not in crudo and "Cl. 93" not in crudo
    assert cifrador.descifrar(doc["direccion_aproximada_cifrada"], "direccion:r-1") == (
        "Cl. 93 # 14, Bogotá"
    )


def test_http_rutas():
    app = create_app(
        config=Configuracion(mongo_uri="mongodb://no-se-usa"),
        servicio=object(),
        reiniciar=lambda: 0,
        catalogo=object(),
        asistencias=object(),
        rutas=_servicio(),
    )
    with TestClient(app) as http:
        r = http.post(
            "/rutas",
            json={"cliente_id": "cli-1", "latitud": 4.67, "longitud": -74.04,
                  "prestador_ids": ["prs-1", "prs-2"]},
        )  # fmt: skip
        assert r.status_code == 200
        assert len(r.json()["rutas"]) == 2
        r = http.post(
            "/rutas",
            json={"cliente_id": "c", "latitud": 99, "longitud": 0, "prestador_ids": []},
        )
        assert r.status_code == 422
