"""Rutas y tiempos de los prestadores (HU-M06, v0.51, B8): simulado, Mapbox, caché y endpoint."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.mapas import (
    AdaptadorMapasMapbox,
    AdaptadorMapasSimulado,
    AdaptadorMapasSinToken,
    CacheMapas,
    codificar_polyline6,
    mapas_desde_entorno,
)
from app.main import app

CLIENTE = (4.6767, -74.0483)
CLINICA = {"id": "prs-bog-001", "latitud": 4.6859, "longitud": -74.0524}
TALLER = {"id": "prs-bog-002", "latitud": 4.6835, "longitud": -74.0322}


def decodificar_polyline6(texto: str) -> list[tuple[float, float]]:
    puntos, i, lat, lon = [], 0, 0, 0
    while i < len(texto):
        valores = []
        for _ in range(2):
            resultado, desplazamiento = 0, 0
            while True:
                b = ord(texto[i]) - 63
                i += 1
                resultado |= (b & 0x1F) << desplazamiento
                desplazamiento += 5
                if b < 0x20:
                    break
            valores.append(~(resultado >> 1) if resultado & 1 else resultado >> 1)
        lat += valores[0]
        lon += valores[1]
        puntos.append((lat / 1e6, lon / 1e6))
    return puntos


def test_polyline6_ida_y_vuelta():
    puntos = [(4.6859, -74.0524), (4.6767, -74.0483), (-33.4489, -70.6693)]
    assert decodificar_polyline6(codificar_polyline6(puntos)) == puntos


def test_simulado_con_las_reglas_de_la_app_y_geometria_solo_a_los_pedidos():
    rutas = AdaptadorMapasSimulado().rutas(CLIENTE, [CLINICA, TALLER], {"prs-bog-001"})
    clinica, taller = rutas
    assert clinica["duracion_segundos"] == 6 * 60  # ceil(1,1 km a 20 km/h = 3,3) + 2
    assert clinica["distancia_metros"] == pytest.approx(1100, abs=50)
    assert clinica["estimado"] is True
    assert decodificar_polyline6(clinica["geometria"]) == [
        (CLINICA["latitud"], CLINICA["longitud"]),
        CLIENTE,
    ]
    assert taller["geometria"] is None


def test_mapbox_matrix_y_directions():
    pedidos = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        pedidos.append(peticion.url)
        if "directions-matrix" in peticion.url.path:
            return httpx.Response(
                200, json={"durations": [[300.4], [None]], "distances": [[1234.6], [None]]}
            )
        return httpx.Response(200, json={"routes": [{"geometry": "abc"}]})

    a = AdaptadorMapasMapbox("sk.secreto", httpx.Client(transport=httpx.MockTransport(responder)))
    rutas = a.rutas(CLIENTE, [CLINICA, TALLER], {"prs-bog-001", "prs-bog-002"})
    # El taller sin camino queda fuera: la app usa su línea recta.
    assert rutas == [
        {
            "prestador_id": "prs-bog-001",
            "distancia_metros": 1235,
            "duracion_segundos": 300,
            "geometria": "abc",
            "estimado": False,
        }
    ]
    matriz = pedidos[0]
    assert "driving-traffic" in matriz.path
    assert matriz.params["sources"] == "1;2" and matriz.params["destinations"] == "0"
    assert pedidos[1].params["geometries"] == "polyline6"


def test_proveedor_por_entorno(monkeypatch):
    monkeypatch.delenv("MAPAS_PROVEEDOR", raising=False)
    monkeypatch.delenv("MAPBOX_TOKEN", raising=False)
    assert isinstance(mapas_desde_entorno(), AdaptadorMapasSimulado)
    monkeypatch.setenv("MAPAS_PROVEEDOR", "mapbox")
    monkeypatch.setenv("MAPBOX_TOKEN", "pendiente-de-cargar")
    sin_token = mapas_desde_entorno()
    assert isinstance(sin_token, AdaptadorMapasSinToken)
    with pytest.raises(RuntimeError):
        sin_token.rutas(CLIENTE, [CLINICA], set())
    monkeypatch.setenv("MAPBOX_TOKEN", "sk.real")
    assert isinstance(mapas_desde_entorno(), AdaptadorMapasMapbox)


def test_cache_por_celda_sin_coordenadas_en_la_llave():
    class Almacen(dict):
        def set(self, nombre, valor, ex=None):
            self[nombre] = valor

    almacen = Almacen()
    cache = CacheMapas(almacen)
    cache.guardar(CLIENTE, ["a"], set(), [{"prestador_id": "a"}])
    assert "4.67" not in next(iter(almacen))
    # A unos 50 m cae en la misma celda.
    assert cache.obtener((4.6769, -74.0484), ["a"], set()) == [{"prestador_id": "a"}]
    assert cache.obtener((4.70, -74.0483), ["a"], set()) is None


def test_endpoints_simulados():
    with TestClient(app) as c:
        r = c.post(
            "/mapas/rutas",
            json={
                "latitud": CLIENTE[0],
                "longitud": CLIENTE[1],
                "destinos": [CLINICA, TALLER],
                "con_geometria": ["prs-bog-001"],
            },
        )
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["proveedor"] == "simulado" and cuerpo["resultado"] == "OK"
        assert [x["prestador_id"] for x in cuerpo["rutas"]] == ["prs-bog-001", "prs-bog-002"]
        inversa = c.post("/lugares/inversa", json={"latitud": 4.6767, "longitud": -74.0483})
        assert inversa.json()["direccion"].endswith("(simulada)")
        assert "mapas" in c.get("/circuitos").json()


def test_rutas_con_el_proveedor_caido_no_dan_5xx(monkeypatch):
    with TestClient(app) as c:
        monkeypatch.setattr(
            "app.adapters.mapas.AdaptadorMapasSimulado.rutas",
            lambda *a: (_ for _ in ()).throw(ConnectionError("caído")),
        )
        r = c.post(
            "/mapas/rutas",
            json={"latitud": 1.0, "longitud": 1.0, "destinos": [CLINICA], "con_geometria": []},
        )
    assert r.status_code == 200
    assert r.json()["resultado"] in ("FALLA", "CIRCUITO_ABIERTO")
    assert r.json()["rutas"] == []
