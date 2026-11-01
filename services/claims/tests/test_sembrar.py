import json
from pathlib import Path

from fakes import AHORA, AlmacenamientoFalso, PolicyFalso, RepositorioFalso
from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.application.servicio_siniestros import ServicioSiniestros
from app.main import create_app
from app.sembrar import sembrar
from app.semilla_aleatoria import generar

# Los datos de la semilla: los 10 casos del dummy de siniestros, ya retirado, más los generados.
DATOS = Path(__file__).resolve().parents[1] / "semilla"
TOTAL = 20
BASES = json.loads((DATOS / "siniestros.json").read_text(encoding="utf-8"))


def _sembrado():
    repo, almacen, policy = RepositorioFalso(), AlmacenamientoFalso(), PolicyFalso()
    total = sembrar(repo, DATOS, lambda: AHORA, almacen, policy)
    return repo, almacen, policy, total


def _evidencias_esperadas() -> int:
    casos = [*BASES, *generar(BASES, AHORA)]
    return sum(len(c["evidencias"]) for c in casos)


def test_siembra_todos_los_casos_con_poliza_real_y_evidencias_en_el_almacenamiento():
    repo, almacen, policy, total = _sembrado()
    assert total == TOTAL and len(repo.siniestros) == TOTAL
    assert repo.siniestros["sin-01"]["poliza_id"] == "poliza-de-demo-siniestros-prop-01"
    assert len(almacen.subidas) == _evidencias_esperadas()
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
    assert len(almacen.subidas) == _evidencias_esperadas()
    assert len(policy.consultas) == TOTAL


def test_el_reinicio_borra_lo_que_hizo_la_demo_sin_llamar_a_otros_servicios():
    repo, *_ = _sembrado()
    repo.resoluciones_por["sin-01"] = {"tipo": "RECHAZO"}
    repo.siniestros["sin-01"]["estado"] = "RECHAZADO"
    assert sembrar(repo, DATOS, lambda: AHORA) == TOTAL
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


def test_ningun_caso_en_revision_queda_sin_perito():
    repo, *_ = _sembrado()
    en_revision = [s for s in repo.siniestros.values() if s["estado"] == "EN_REVISION"]
    assert len(en_revision) >= 6
    assert all(repo.asignaciones_por[s["id"]]["perito_id"] for s in en_revision)


def test_los_casos_generados_son_consistentes_y_repetibles():
    casos = generar(BASES, AHORA)
    assert casos == generar(BASES, AHORA)
    assert len(casos) == 10
    assert len({c["radicado"] for c in [*BASES, *casos]}) == TOTAL
    for caso in casos:
        assert caso["ocurrido_en"] < caso["notificado_en"]
        resolucion = caso["resolucion"]
        if caso["estado"] == "PENDIENTE":
            assert resolucion is None
        elif caso["estado"] == "APROBADO":
            indemnizacion = int(resolucion["indemnizacion"]["valor"])
            assert 0 < indemnizacion <= int(caso["suma_reclamada"]["valor"])
        elif caso["estado"] == "RECHAZADO":
            assert resolucion["motivo"]
            assert not all(v["cumple"] for v in caso["verificaciones"])
            continue
        else:
            assert resolucion["tipo"] == "DERIVACION" and resolucion["perito_id"]
        assert all(v["cumple"] for v in caso["verificaciones"])
    archivos = {f.name for f in (DATOS / "medios").iterdir()}
    assert {e["archivo"] for c in casos for e in c["evidencias"]} <= archivos
