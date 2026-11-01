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
