"""KAN-259 (HU-M04, Fase 4, D8): la semilla `sembrar-polizas-parametricas`."""

from fakes import RepositorioCoberturasParametricasFalso, RepositorioPropuestasFalso

from app.sembrar_polizas_parametricas import sembrar


def test_crea_dos_vuelos_y_una_celda_de_clima():
    repo = RepositorioPropuestasFalso()
    coberturas = RepositorioCoberturasParametricasFalso()
    creadas = sembrar(repo, coberturas)
    assert len(creadas) == 3
    tipos = sorted(c["tipo"] for c in creadas)
    assert tipos == ["CLIMA", "VUELO", "VUELO"]
    numeros = {c.get("numero_vuelo") for c in creadas if c["tipo"] == "VUELO"}
    assert numeros == {"AV9340", "AV1234"}


def test_es_idempotente_no_duplica_coberturas():
    repo = RepositorioPropuestasFalso()
    coberturas = RepositorioCoberturasParametricasFalso()
    primera = sembrar(repo, coberturas)
    segunda = sembrar(repo, coberturas)
    assert primera == segunda
    assert len(coberturas._coberturas) == 3  # noqa: SLF001 (prueba interna del doble)


def test_las_polizas_quedan_emitidas():
    repo = RepositorioPropuestasFalso()
    coberturas = RepositorioCoberturasParametricasFalso()
    sembrar(repo, coberturas)
    propuesta = repo.obtener("demo-m04-vuelo-prop-01")
    assert propuesta["estado"] == "EMITIDA"
