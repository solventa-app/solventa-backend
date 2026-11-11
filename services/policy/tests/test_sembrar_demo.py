import json

from fakes import RepositorioPropuestasFalso

from app.sembrar_demo import DATOS, sembrar


def _polizas():
    return json.loads(DATOS.read_text(encoding="utf-8"))


def test_crea_las_polizas_emitidas_con_ids_de_propuesta_fijos():
    repo = RepositorioPropuestasFalso()
    creadas = sembrar(repo, _polizas())
    assert len(creadas) == 20
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


def test_el_cliente_demo_tiene_tambien_una_poliza_de_auto():
    """KAN-290: la póliza de auto del cliente demo, del mismo titular que su póliza de vida."""
    from app.sembrar_demo import poliza_auto_cliente_demo, poliza_cliente_demo

    auto = poliza_auto_cliente_demo("1090000001", "clave")
    assert auto["cliente_id"] == poliza_cliente_demo("1090000001", "clave")["cliente_id"]
    repo = RepositorioPropuestasFalso()
    sembrar(repo, [auto])
    propuesta = repo.obtener("demo-auto-prop-01")
    assert propuesta["estado"] == "EMITIDA" and propuesta["producto"] == "AUTO"
    assert propuesta["vehiculo"]["marca"] == "MAZDA" and len(propuesta["coberturas"]) == 6
