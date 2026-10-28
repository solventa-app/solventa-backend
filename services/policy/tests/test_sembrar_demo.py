import json

from fakes import RepositorioPropuestasFalso

from app.sembrar_demo import DATOS, sembrar


def _polizas():
    return json.loads(DATOS.read_text(encoding="utf-8"))


def test_crea_diez_polizas_emitidas_con_ids_de_propuesta_fijos():
    repo = RepositorioPropuestasFalso()
    creadas = sembrar(repo, _polizas())
    assert len(creadas) == 10
    assert creadas[0]["propuesta_id"] == "demo-siniestros-prop-01"
    assert all(c["poliza_id"] for c in creadas)
    propuesta = repo.obtener("demo-siniestros-prop-01")
    assert propuesta["estado"] == "EMITIDA"
    assert propuesta["plazo_meses"] == 12


def test_es_idempotente():
    repo = RepositorioPropuestasFalso()
    primera = sembrar(repo, _polizas())
    segunda = sembrar(repo, _polizas())
    assert primera == segunda
