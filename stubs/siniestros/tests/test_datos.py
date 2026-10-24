"""Los datos de prueba son coherentes entre sí y con el contrato de pólizas que copian."""

import json

from app.repositorio import DATOS

SINIESTROS = json.loads((DATOS / "siniestros.json").read_text(encoding="utf-8"))
POLIZAS = json.loads((DATOS / "polizas.json").read_text(encoding="utf-8"))
PERITOS = json.loads((DATOS / "peritos.json").read_text(encoding="utf-8"))
CONTRATO = json.loads((DATOS / "contrato-poliza.json").read_text(encoding="utf-8"))
CREDITOS = (DATOS / "CREDITOS.md").read_text(encoding="utf-8")


def test_hay_10_siniestros_con_entre_2_y_7_evidencias_y_archivos_existentes():
    assert len(SINIESTROS) == 10
    for s in SINIESTROS:
        assert 2 <= len(s["evidencias"]) <= 7, s["id"]
        for e in s["evidencias"]:
            assert (DATOS / "medios" / e["archivo"]).is_file(), e["archivo"]


def test_cada_foto_tiene_su_credito():
    for s in SINIESTROS:
        for e in s["evidencias"]:
            if e["tipo"] == "FOTO":
                assert f"`{e['archivo']}`" in CREDITOS, e["archivo"]


def test_no_hay_estado_derivado():
    permitidos = {"PENDIENTE", "EN_REVISION", "APROBADO", "RECHAZADO"}
    assert {s["estado"] for s in SINIESTROS} <= permitidos


def test_resoluciones_coherentes_con_el_estado():
    tipos = {"APROBADO": "APROBACION", "RECHAZADO": "RECHAZO"}
    peritos = {p["id"] for p in PERITOS}
    for s in SINIESTROS:
        r = s["resolucion"]
        if s["estado"] in tipos:
            assert r and r["tipo"] == tipos[s["estado"]], s["id"]
        if r and r["tipo"] == "DERIVACION":
            assert s["estado"] == "EN_REVISION" and r["perito_id"] in peritos


def test_radicados_unicos_y_polizas_existentes():
    assert len({s["radicado"] for s in SINIESTROS}) == len(SINIESTROS)
    polizas = {p["id"] for p in POLIZAS}
    assert all(s["poliza_id"] in polizas for s in SINIESTROS)


def test_polizas_cumplen_el_contrato_de_datos_que_copian():
    campos = set(CONTRATO["campos_polizas_json"])
    columnas = {c["columna"] for c in CONTRATO["tabla_polizas"]}
    assert columnas <= campos  # todas las columnas de la tabla de origen están en el mock
    for p in POLIZAS:
        assert set(p) == campos, p["id"]
        assert isinstance(p["numero"], int)
        assert p["estado"] == "EMITIDA"
        assert p["vigencia_desde"] < p["vigencia_hasta"]
