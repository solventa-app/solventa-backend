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


def test_demo_staging_crea_un_vuelo_diario_y_clima_para_el_cliente():
    from app.sembrar_polizas_parametricas import sembrar_demo_staging

    repo = RepositorioPropuestasFalso()
    coberturas = RepositorioCoberturasParametricasFalso()
    creadas = sembrar_demo_staging(repo, coberturas, "usuario-felipe-1234", "titular-1", "AV19", 5)
    assert [c["tipo"] for c in creadas] == ["VUELO", "CLIMA"]
    assert len(coberturas._coberturas) == 6  # noqa: SLF001 (5 días de vuelo + 1 de clima)
    # El titular es el `cliente_id` (lo que filtra `miCoberturaParametrica`) y el usuario de AUTH es
    # quien la aceptó (a quien CLAIMS le manda el push y a quien el BFF le muestra `indemnizacion`).
    propuestas = list(repo._por_oferta.values())  # noqa: SLF001
    assert {p["cliente_id"] for p in propuestas} == {"titular-1"}
    assert {p["actor_usuario_id"] for p in propuestas} == {"usuario-felipe-1234"}
    otra = sembrar_demo_staging(repo, coberturas, "usuario-felipe-1234", "titular-1", "AV19", 5)
    assert otra == creadas
    assert len(coberturas._coberturas) == 6  # noqa: SLF001


def test_el_comodin_agrega_una_sola_cobertura_que_calza_con_cualquier_vuelo():
    """Demo de vuelo que siempre paga: la póliza de una corrida anterior (vuelo fijo) gana el
    comodín, una sola vez, y el cruce lo encuentra con cualquier vuelo y fecha."""
    from datetime import date

    from app.sembrar_polizas_parametricas import sembrar_demo_staging

    repo = RepositorioPropuestasFalso()
    coberturas = RepositorioCoberturasParametricasFalso()
    sembrar_demo_staging(repo, coberturas, "usuario-felipe-1234", "titular-1", "AV19", 2)
    for _ in range(2):
        sembrar_demo_staging(repo, coberturas, "usuario-felipe-1234", "titular-1", "RETRASADO", 90)
    numeros = [c["numero_vuelo"] for c in coberturas._coberturas.values()]  # noqa: SLF001
    assert numeros.count("RETRASADO") == 1 and numeros.count("AV19") == 2
    calzan = coberturas.vigentes_de_vuelo("LA77", date(2027, 3, 1))
    assert [c["numero_vuelo"] for c in calzan] == ["RETRASADO"]


def test_varios_clientes_tienen_cada_uno_su_poliza():
    from app.sembrar_polizas_parametricas import sembrar_demo_staging

    repo = RepositorioPropuestasFalso()
    coberturas = RepositorioCoberturasParametricasFalso()
    for usuario in ("usuario-felipe-1234", "usuario-frans-56789", "usuario-daniel-0001"):
        sembrar_demo_staging(repo, coberturas, usuario, usuario, "RETRASADO", 90)
    duenos = {p["actor_usuario_id"] for p in repo._por_oferta.values()}  # noqa: SLF001
    assert len(duenos) == 3
    assert len(coberturas.vigentes_de_celda(4.62, -74.09)) == 3


class _AuthFalso:
    def usuario_de(self, correo: str) -> str | None:
        return {"a@ejemplo.co": "usuario-a", "b@ejemplo.co": "usuario-b"}.get(correo)


def test_los_correos_se_resuelven_a_usuarios_y_el_que_no_tiene_cuenta_se_salta(caplog):
    import logging

    from app.sembrar_polizas_parametricas import clientes_de_la_semilla

    entorno = {
        "SEMILLA_CLIENTES": "usuario-a",
        "SEMILLA_CORREOS": "a@ejemplo.co, b@ejemplo.co,nadie@ejemplo.co",
    }
    with caplog.at_level(logging.INFO):
        assert clientes_de_la_semilla(entorno, _AuthFalso()) == ["usuario-a", "usuario-b"]
    assert "ejemplo.co" not in caplog.text
    assert clientes_de_la_semilla({"SEMILLA_CLIENTE_ID": "u-1"}, _AuthFalso()) == ["u-1"]
