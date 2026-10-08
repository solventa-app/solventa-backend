"""Pruebas de `ejecutar_una_pasada` (T-W05-4) sin `asyncio` ni esperar ningún
`intervalo_s` — se invoca la función directamente, igual que haría el bucle
en background en cada tick."""

from fakes import BusEventosFalso, ClienteAclFalso, RepositorioCobrosFalso

from app.application.cliente_acl import ResultadoCobro
from app.application.reintento import ejecutar_una_pasada
from app.application.servicio_cobros import ServicioCobros

ORDEN = {
    "clave_idempotencia": "clave-reintento-1",
    "poliza_id": "poliza-9",
    "monto": "50.00",
    "moneda": "COP",
    "token_medio_pago": "tok_visa_0001",
}


def test_reintento_reutiliza_la_misma_clave_de_idempotencia() -> None:
    """CA-W05-05: el reintento reutiliza la clave de idempotencia."""
    repositorio = RepositorioCobrosFalso()
    resultado_inicial = ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")
    cliente_acl_inicial = ClienteAclFalso([resultado_inicial])
    ServicioCobros(repositorio, cliente_acl_inicial, BusEventosFalso()).crear_cobro(dict(ORDEN))

    resultado_reintento = ResultadoCobro(estado="cobrado", referencia="ref-reintento")
    cliente_acl_reintento = ClienteAclFalso([resultado_reintento])
    bus = BusEventosFalso()

    procesados = ejecutar_una_pasada(repositorio, cliente_acl_reintento, bus, max_intentos=10)

    assert procesados == 1
    assert cliente_acl_reintento.ordenes[0].clave_idempotencia == ORDEN["clave_idempotencia"]
    assert bus.eventos[-1]["datos"]["estado"] == "cobrado"


def test_reintento_resuelve_un_cobro_pendiente_a_cobrado() -> None:
    repositorio = RepositorioCobrosFalso()
    ServicioCobros(
        repositorio,
        ClienteAclFalso([ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")]),
        BusEventosFalso(),
    ).crear_cobro(dict(ORDEN))

    assert repositorio.listar_pendientes(max_intentos=10)[0]["estado"] == "pendiente"

    cliente_acl = ClienteAclFalso([ResultadoCobro(estado="cobrado")])
    ejecutar_una_pasada(repositorio, cliente_acl, BusEventosFalso(), max_intentos=10)

    assert repositorio.listar_pendientes(max_intentos=10) == []
    assert repositorio.balance_poliza(ORDEN["poliza_id"]) == "50.00"


def test_reintento_incrementa_intentos_y_se_acota() -> None:
    """Acotado: una vez agotado `max_intentos`, el cobro deja de aparecer en
    `listar_pendientes` — el bucle de reintento deja de tocarlo (queda
    `pendiente` para seguimiento manual)."""
    repositorio = RepositorioCobrosFalso()
    ServicioCobros(
        repositorio,
        ClienteAclFalso([ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")]),
        BusEventosFalso(),
    ).crear_cobro(dict(ORDEN))

    resultado_siempre_pendiente = ResultadoCobro(estado="pendiente", motivo="sigue_caida")
    cliente_acl_siempre_pendiente = ClienteAclFalso([resultado_siempre_pendiente])
    bus = BusEventosFalso()

    # intentos arranca en 1 (al crear); con max_intentos=3 debe poder
    # reintentar 2 veces más antes de agotarse.
    assert ejecutar_una_pasada(repositorio, cliente_acl_siempre_pendiente, bus, max_intentos=3) == 1
    assert ejecutar_una_pasada(repositorio, cliente_acl_siempre_pendiente, bus, max_intentos=3) == 1
    assert ejecutar_una_pasada(repositorio, cliente_acl_siempre_pendiente, bus, max_intentos=3) == 0

    cobro = next(iter(repositorio._por_id.values()))
    assert cobro["estado"] == "pendiente"
    assert cobro["intentos"] == 3


def test_pasada_sin_pendientes_no_llama_al_acl() -> None:
    repositorio = RepositorioCobrosFalso()
    cliente_acl = ClienteAclFalso()

    procesados = ejecutar_una_pasada(repositorio, cliente_acl, BusEventosFalso(), max_intentos=10)

    assert procesados == 0
    assert cliente_acl.llamadas == 0
