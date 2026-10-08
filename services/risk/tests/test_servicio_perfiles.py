from fakes import BusEventosFalso, RepositorioPerfilesFalso

from app.application.servicio_perfiles import ServicioPerfiles

FUENTES = [
    {
        "fuente": "cuentas-bancarias",
        "datos": {"saldo": 100},
        "capturado_en": "2026-10-07T12:00:00+00:00",
        "de_cache": False,
        "degradado": False,
    }
]


def test_registrar_devuelve_version_1_en_el_primer_perfil() -> None:
    servicio = ServicioPerfiles(RepositorioPerfilesFalso(), BusEventosFalso())

    resultado = servicio.registrar("cliente-1", FUENTES)

    assert resultado["cliente_id"] == "cliente-1"
    assert resultado["perfil_version"] == 1
    assert resultado["operation_time"] == "1700000001.1"


def test_registrar_incrementa_version_por_cliente() -> None:
    repositorio = RepositorioPerfilesFalso()
    servicio = ServicioPerfiles(repositorio, BusEventosFalso())

    servicio.registrar("cliente-1", FUENTES)
    segundo = servicio.registrar("cliente-1", FUENTES)
    otro_cliente = servicio.registrar("cliente-2", FUENTES)

    assert segundo["perfil_version"] == 2
    assert otro_cliente["perfil_version"] == 1  # secuencia independiente por cliente


def test_registrar_publica_el_evento_perfil_actualizado() -> None:
    bus = BusEventosFalso()
    servicio = ServicioPerfiles(RepositorioPerfilesFalso(), bus)

    resultado = servicio.registrar("cliente-1", FUENTES)

    assert len(bus.eventos) == 1
    evento = bus.eventos[0]
    assert evento["tipo"] == "perfil.actualizado"
    assert evento["datos"]["clienteId"] == "cliente-1"
    assert evento["datos"]["perfilVersion"] == resultado["perfil_version"]
    assert evento["datos"]["operationTime"] == resultado["operation_time"]


def test_registrar_pasa_las_fuentes_tal_cual_al_repositorio() -> None:
    repositorio = RepositorioPerfilesFalso()
    servicio = ServicioPerfiles(repositorio, BusEventosFalso())

    servicio.registrar("cliente-1", FUENTES)

    assert repositorio.guardados == [("cliente-1", FUENTES)]
