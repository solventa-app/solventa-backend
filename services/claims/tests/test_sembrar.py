from pathlib import Path

from fakes import AHORA, AlmacenamientoFalso, PolicyFalso, RepositorioFalso
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.servicio_siniestros import ServicioSiniestros
from app.main import create_app
from app.sembrar import sembrar

# Los datos de la semilla (los mismos 10 casos que tenía el dummy de siniestros, ya retirado).
DATOS = Path(__file__).resolve().parents[1] / "semilla"


def _sembrado():
    repo, almacen, policy = RepositorioFalso(), AlmacenamientoFalso(), PolicyFalso()
    total = sembrar(repo, DATOS, lambda: AHORA, almacen, policy)
    return repo, almacen, policy, total


def test_siembra_los_diez_casos_con_poliza_real_y_evidencias_en_el_almacenamiento():
    repo, almacen, policy, total = _sembrado()
    assert total == 10 and len(repo.siniestros) == 10
    assert repo.siniestros["sin-01"]["poliza_id"] == "poliza-de-demo-siniestros-prop-01"
    assert len(almacen.subidas) == 37
    assert {s[0] for s in almacen.subidas} == {"siniestro-foto", "siniestro-documento"}
    assert all(s[3].startswith("sin-") for s in almacen.subidas)  # propietario = el siniestro
    assert len(repo.peritos_por) == 4


def test_las_resoluciones_de_la_semilla_quedan_en_sus_colecciones():
    repo, *_ = _sembrado()
    assert repo.asignaciones_por["sin-04"]["perito_id"] == "per-04"
    assert repo.resoluciones_por["sin-05"]["tipo"] == "APROBACION"
    assert repo.resoluciones_por["sin-09"]["motivo"] == "POLIZA_NO_VIGENTE"
    assert "sin-01" not in repo.resoluciones_por


def test_las_fotos_geoetiquetadas_llevan_las_coordenadas_del_caso():
    repo, *_ = _sembrado()
    foto = next(e for e in repo.evidencias_por["sin-01"] if e["tipo"] == "FOTO")
    assert foto["geoetiquetada"] is True and foto["latitud"] == 4.7236
    doc = next(e for e in repo.evidencias_por["sin-01"] if e["tipo"] == "DOCUMENTO")
    assert doc["latitud"] is None


def test_segunda_siembra_no_vuelve_a_subir_ni_a_pedir_polizas():
    repo, almacen, policy, _ = _sembrado()
    sembrar(repo, DATOS, lambda: AHORA, almacen, policy)
    assert len(almacen.subidas) == 37
    assert len(policy.consultas) == 10


def test_el_reinicio_borra_lo_que_hizo_la_demo_sin_llamar_a_otros_servicios():
    repo, *_ = _sembrado()
    repo.resoluciones_por["sin-01"] = {"tipo": "RECHAZO"}
    repo.siniestros["sin-01"]["estado"] = "RECHAZADO"
    assert sembrar(repo, DATOS, lambda: AHORA) == 10
    assert "sin-01" not in repo.resoluciones_por
    assert repo.siniestros["sin-01"]["estado"] == "PENDIENTE"


def test_el_endpoint_de_reinicio_solo_existe_en_modo_demo():
    servicio = ServicioSiniestros(RepositorioFalso())
    apagado = create_app(Configuracion(mongo_uri="x"), servicio, reiniciar=lambda: 10)
    encendido = create_app(
        Configuracion(mongo_uri="x", demo_reiniciar=True), servicio, reiniciar=lambda: 10
    )
    with TestClient(apagado) as c:
        assert c.post("/_demo/reiniciar").status_code == 404
    with TestClient(encendido) as c:
        assert c.post("/_demo/reiniciar").json() == {"siniestros": 10}
