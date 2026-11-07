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
