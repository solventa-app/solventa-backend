from fastapi.testclient import TestClient

from app.adapters.lugares import AdaptadorLugaresStub
from app.main import app


def test_stub_autocompleta_y_valida():
    a = AdaptadorLugaresStub()
    [s] = a.autocompletar("Calle 10 # 5-20", "co")
    r = a.validar(s["place_id"])
    assert r["verificado"] and r["pais"] == "CO"


def test_stub_rechaza_pais_no_soportado_y_place_invalido():
    a = AdaptadorLugaresStub()
    assert a.autocompletar("Calle 10", "FR") == []
    assert a.validar("stub:FR:x")["verificado"] is False


def test_endpoints_sin_llave():
    c = TestClient(app)
    j = c.get("/lugares/autocompletar", params={"texto": "Carrera 7", "pais": "CO"}).json()
    assert j["sugerencias"]
    assert c.get("/lugares/validar", params={"place_id": "nada"}).json()["verificado"] is False


def test_proveedor_por_entorno(monkeypatch):
    from app.adapters.lugares import (
        AdaptadorLugaresGoogle,
        AdaptadorLugaresSinLlave,
        lugares_desde_entorno,
    )

    monkeypatch.delenv("PLACES_PROVEEDOR", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    assert isinstance(lugares_desde_entorno(), AdaptadorLugaresStub)  # local y CI

    # T19: en la nube nunca el stub; con el secreto aún en su valor marcador, «sin verificar».
    monkeypatch.setenv("PLACES_PROVEEDOR", "google")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "pendiente-de-cargar")
    sin_llave = lugares_desde_entorno()
    assert isinstance(sin_llave, AdaptadorLugaresSinLlave)
    assert sin_llave.autocompletar("Calle 10", "CO") == []
    assert sin_llave.validar("stub:CO:Calle 10")["verificado"] is False

    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "llave-real")
    assert isinstance(lugares_desde_entorno(), AdaptadorLugaresGoogle)


# ---------- HU-M06 (v0.50, B9): ubicar la dirección escrita ----------


def test_stub_ubica_cerca_de_bogota_y_no_encuentra_lo_que_no_existe():
    a = AdaptadorLugaresStub()
    [s] = a.autocompletar("Cra 45 # 22-18", "CO")
    r = a.ubicar(s["place_id"], None, "es")
    assert r["encontrada"] is True and r["codigo"] is None
    assert abs(r["latitud"] - 4.6767) < 0.01 and abs(r["longitud"] + 74.0483) < 0.01
    assert r == a.ubicar(None, "Cra 45 # 22-18", "en")  # determinista
    assert a.ubicar(None, "calle noexiste", "es")["codigo"] == "DIRECCION_NO_ENCONTRADA"
    assert a.ubicar("stub:FR:x", None, "es")["encontrada"] is False


def test_google_ubica_por_place_id_o_por_texto_en_el_idioma_de_la_app():
    import httpx

    from app.adapters.lugares import AdaptadorLugaresGoogle

    pedidos = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        pedidos.append(peticion.url)
        resultado = {
            "formatted_address": "Cra. 45 #22-18, Bogotá, Colombia",
            "geometry": {"location": {"lat": 4.63, "lng": -74.09}},
        }
        if peticion.url.path.endswith("/geocode/json"):
            return httpx.Response(200, json={"results": [resultado]})
        return httpx.Response(200, json={"result": resultado})

    a = AdaptadorLugaresGoogle(
        "llave", cliente=httpx.Client(transport=httpx.MockTransport(responder))
    )
    r = a.ubicar("ChIJ123", None, "en")
    assert r == {
        "encontrada": True,
        "latitud": 4.63,
        "longitud": -74.09,
        "direccion": "Cra. 45 #22-18, Bogotá, Colombia",
        "codigo": None,
    }
    assert pedidos[0].params["language"] == "en"
    assert "geometry" in pedidos[0].params["fields"]
    a.ubicar(None, "Cra 45 # 22-18", "es")
    assert pedidos[1].path.endswith("/geocode/json")
    assert pedidos[1].params["components"] == "country:CO"


def test_google_sin_resultados_no_encuentra_y_si_cae_lanza():
    import httpx
    import pytest

    from app.adapters.lugares import AdaptadorLugaresGoogle

    vacio = AdaptadorLugaresGoogle(
        "llave",
        cliente=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []}))
        ),
    )
    assert vacio.ubicar(None, "xyz", "es")["codigo"] == "DIRECCION_NO_ENCONTRADA"
    caido = AdaptadorLugaresGoogle(
        "llave",
        cliente=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))),
    )
    with pytest.raises(httpx.HTTPError):
        caido.ubicar(None, "xyz", "es")


def test_cache_por_idioma_sin_guardar_el_texto_ni_los_no_encontrados():
    from app.adapters.lugares import CacheLugares, encontrada, no_encontrada

    class Almacen(dict):
        def set(self, nombre, valor, ex=None):
            self[nombre] = valor

    almacen = Almacen()
    cache = CacheLugares(almacen)
    cache.guardar(None, "Cra 45 # 22-18", "es", encontrada(4.6, -74.0, "Cra 45"))
    cache.guardar(None, "otra", "es", no_encontrada())
    assert len(almacen) == 1
    assert "Cra 45" not in next(iter(almacen))  # la llave es un hash
    assert cache.obtener(None, "cra 45 # 22-18", "es")["latitud"] == 4.6
    assert cache.obtener(None, "Cra 45 # 22-18", "en") is None


def test_endpoint_ubicar_sin_llave_y_sin_datos():
    with TestClient(app) as c:
        r = c.post("/lugares/ubicar", json={"texto": "Cra 45 # 22-18", "idioma": "en"})
        assert r.status_code == 200 and r.json()["encontrada"] is True
        r = c.post("/lugares/ubicar", json={})
        assert r.json()["codigo"] == "DIRECCION_NO_ENCONTRADA"
        assert "lugares" in c.get("/circuitos").json()
