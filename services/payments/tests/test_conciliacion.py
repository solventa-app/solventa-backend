"""Conciliación y reintento (KAN-28, CA-W05-02): conciliar antes de cobrar, reintento
manual por estado y no gastar intentos cuando la pasarela ni se intentó."""

import logging

import pytest
from fakes import BusEventosFalso, ClienteAclFalso, RepositorioCobrosFalso

from app.application.cliente_acl import ResultadoCobro, ResultadoConsulta
from app.application.reintento import ejecutar_una_pasada
from app.application.servicio_cobros import NoReintentable, ServicioCobros

ORDEN = {
    "id_intento_cobro": "clave-concilia-1",
    "poliza_id": "POL-CONCILIA-1",
    "monto": "80000.00",
    "moneda": "COP",
    "token_medio_pago": "tok_demo",
}
PENDIENTE = ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")


def _cobro_pendiente() -> tuple[RepositorioCobrosFalso, dict]:
    """Un cobro que quedó `pendiente` (p. ej. la pasarela respondió tarde)."""
    repositorio = RepositorioCobrosFalso()
    cobro = ServicioCobros(
        repositorio, ClienteAclFalso([PENDIENTE]), BusEventosFalso()
    ).crear_cobro(dict(ORDEN))
    return repositorio, cobro


# ---------- conciliación (procesar_reintento, vía el bucle y vía el botón)


def test_si_la_pasarela_ya_lo_cobro_se_concilia_sin_cobrar_de_nuevo() -> None:
    """Hallazgo 4 de la demo: la pasarela cobró, pero el ACL cortó a los 700 ms."""
    repositorio, cobro = _cobro_pendiente()
    ya_cobrado = ResultadoConsulta(
        resultado="encontrado", estado="cobrado", referencia="pg-original"
    )
    acl = ClienteAclFalso(en_pasarela={ORDEN["id_intento_cobro"]: ya_cobrado})
    bus = BusEventosFalso()

    actualizado = ServicioCobros(repositorio, acl, bus).reintentar(cobro["id"])

    assert actualizado["estado"] == "cobrado"
    assert actualizado["referencia"] == "pg-original"
    assert acl.llamadas == 0  # 0 cobros nuevos: no hay doble cobro
    assert actualizado["intentos"] == cobro["intentos"]  # no se cobró, no cuenta intento
    assert bus.eventos[-1]["datos"]["estado"] == "cobrado"


def test_si_la_pasarela_no_lo_tiene_se_cobra_con_la_misma_clave() -> None:
    repositorio, cobro = _cobro_pendiente()
    acl = ClienteAclFalso([ResultadoCobro(estado="cobrado", referencia="pg-nuevo")])

    actualizado = ServicioCobros(repositorio, acl, BusEventosFalso()).reintentar(cobro["id"])

    assert acl.consultas == [ORDEN["id_intento_cobro"]]  # primero concilia
    assert acl.ordenes[0].id_intento_cobro == ORDEN["id_intento_cobro"]
    assert actualizado["estado"] == "cobrado"
    assert actualizado["intentos"] == cobro["intentos"] + 1


def test_si_no_se_puede_preguntar_no_se_cobra_ni_se_gasta_intento() -> None:
    repositorio, cobro = _cobro_pendiente()
    acl = ClienteAclFalso(consulta_desconocida=True)
    bus = BusEventosFalso()

    actualizado = ServicioCobros(repositorio, acl, bus).reintentar(cobro["id"])

    assert actualizado["estado"] == "pendiente"
    assert actualizado["intentos"] == cobro["intentos"]
    assert acl.llamadas == 0
    assert bus.eventos == []  # sin cambio de estado, sin evento


def test_el_bucle_tambien_concilia_antes_de_cobrar() -> None:
    repositorio, _ = _cobro_pendiente()
    ya_cobrado = ResultadoConsulta(
        resultado="encontrado", estado="cobrado", referencia="pg-original"
    )
    acl = ClienteAclFalso(en_pasarela={ORDEN["id_intento_cobro"]: ya_cobrado})

    ejecutar_una_pasada(repositorio, acl, BusEventosFalso(), max_intentos=10)

    assert acl.llamadas == 0
    assert repositorio.listar(ORDEN["poliza_id"], None)[0]["referencia"] == "pg-original"


def test_el_bucle_no_gasta_intentos_con_el_circuito_abierto() -> None:
    """Hallazgo 3 de la demo: una caída larga ya no agota los intentos del cobro."""
    repositorio, cobro = _cobro_pendiente()
    circuito_abierto = ResultadoCobro(estado="pendiente", motivo="circuito_abierto")
    acl = ClienteAclFalso([circuito_abierto])

    for _ in range(20):  # mucho más que el máximo de intentos
        ejecutar_una_pasada(repositorio, acl, BusEventosFalso(), max_intentos=3)

    pendiente = repositorio.obtener(cobro["id"])
    assert pendiente["intentos"] == cobro["intentos"]
    assert repositorio.listar_pendientes(max_intentos=3)  # el bucle lo sigue tomando


# ---------- reintento manual por estado


def test_reintentar_un_cobrado_lo_devuelve_tal_cual() -> None:
    repositorio = RepositorioCobrosFalso()
    cobrado = ServicioCobros(repositorio, ClienteAclFalso(), BusEventosFalso()).crear_cobro(
        dict(ORDEN)
    )
    acl = ClienteAclFalso()

    resultado = ServicioCobros(repositorio, acl, BusEventosFalso()).reintentar(cobrado["id"])

    assert resultado["estado"] == "cobrado"
    assert acl.llamadas == 0 and acl.consultas == []  # doble clic seguro


def test_reintentar_un_rechazado_no_se_permite() -> None:
    repositorio = RepositorioCobrosFalso()
    rechazo = ResultadoCobro(estado="rechazado", motivo="fondos_insuficientes")
    rechazado = ServicioCobros(
        repositorio, ClienteAclFalso([rechazo]), BusEventosFalso()
    ).crear_cobro(dict(ORDEN))

    with pytest.raises(NoReintentable, match="otro medio de pago"):
        ServicioCobros(repositorio, ClienteAclFalso(), BusEventosFalso()).reintentar(
            rechazado["id"]
        )


def test_reintentar_un_cobro_inexistente_devuelve_none() -> None:
    servicio = ServicioCobros(RepositorioCobrosFalso(), ClienteAclFalso(), BusEventosFalso())

    assert servicio.reintentar("no-existe") is None


def test_el_reintento_manual_funciona_aunque_se_agotaron_los_intentos() -> None:
    repositorio, cobro = _cobro_pendiente()
    sigue_caida = ClienteAclFalso([PENDIENTE])
    for _ in range(5):
        ejecutar_una_pasada(repositorio, sigue_caida, BusEventosFalso(), max_intentos=3)
    assert repositorio.listar_pendientes(max_intentos=3) == []  # el bucle ya no lo toma

    acl = ClienteAclFalso([ResultadoCobro(estado="cobrado", referencia="pg-manual")])
    actualizado = ServicioCobros(repositorio, acl, BusEventosFalso()).reintentar(cobro["id"])

    assert actualizado["estado"] == "cobrado"


def test_los_logs_no_llevan_la_poliza_en_crudo(caplog) -> None:
    repositorio, cobro = _cobro_pendiente()

    with caplog.at_level(logging.DEBUG):
        ServicioCobros(repositorio, ClienteAclFalso(), BusEventosFalso()).reintentar(cobro["id"])

    assert caplog.records
    assert ORDEN["poliza_id"] not in caplog.text
