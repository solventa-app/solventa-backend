from fakes import AlmacenClaveValorFalso, BusEventosFalso, RepositorioPerfilesFalso

from app.application.cache_score import CacheScore, clave_oferta, clave_score
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
    assert isinstance(resultado["score"], int)
    assert resultado["nivel_riesgo"] in ("BAJO", "MEDIO", "ALTO")
    assert resultado["fecha_vigencia"]


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
    assert evento["datos"]["nivelRiesgo"] == resultado["nivel_riesgo"]
    assert evento["datos"]["fechaVigencia"] == resultado["fecha_vigencia"]
    assert evento["datos"]["versionReglas"] == "v1"


def test_registrar_pasa_las_fuentes_tal_cual_al_repositorio() -> None:
    repositorio = RepositorioPerfilesFalso()
    servicio = ServicioPerfiles(repositorio, BusEventosFalso())

    servicio.registrar("cliente-1", FUENTES)

    assert repositorio.guardados == [("cliente-1", FUENTES)]


def test_registrar_guarda_score_cacheado_en_redis() -> None:
    almacen = AlmacenClaveValorFalso()
    cache = CacheScore(almacen, ttl_segundos=300)
    servicio = ServicioPerfiles(RepositorioPerfilesFalso(), BusEventosFalso(), cache_score=cache)

    resultado = servicio.registrar("cliente-1", FUENTES)

    en_score = cache.obtener(clave_score("cliente-1", "v1"))
    en_oferta = cache.obtener(clave_oferta("cliente-1", 1, "v1"))
    assert en_score is not None
    assert en_oferta is not None
    assert en_score["score_total"] == resultado["score"]
    assert en_score["nivel_riesgo"] == resultado["nivel_riesgo"]
    assert en_score["fecha_vigencia"] == resultado["fecha_vigencia"]
    assert en_oferta["score_total"] == resultado["score"]
