"""Pruebas del caso de uso `ServicioCobros`, con dobles de prueba (ver
`fakes.py`) — sin PostgreSQL ni ACL Worker reales.

La prueba más importante de esta tarea es la de idempotencia bajo
concurrencia real (dos hilos, no solo dos llamadas secuenciales): ver
`test_llamadas_concurrentes_con_la_misma_clave_cobran_una_sola_vez`."""

import threading

from fakes import BusEventosFalso, ClienteAclFalso, RepositorioCobrosFalso

from app.application.cliente_acl import ResultadoCobro
from app.application.servicio_cobros import ServicioCobros

ORDEN = {
    "clave_idempotencia": "clave-abc-123",
    "poliza_id": "poliza-1",
    "monto": "150.00",
    "moneda": "COP",
    "token_medio_pago": "tok_visa_4242",
}


_Dobles = tuple[ServicioCobros, RepositorioCobrosFalso, ClienteAclFalso, BusEventosFalso]


def _servicio(cliente_acl=None) -> _Dobles:
    repositorio = RepositorioCobrosFalso()
    cliente_acl = cliente_acl or ClienteAclFalso()
    bus = BusEventosFalso()
    return ServicioCobros(repositorio, cliente_acl, bus), repositorio, cliente_acl, bus


def test_crear_cobro_llama_al_acl_y_queda_cobrado() -> None:
    servicio, _, cliente_acl, _ = _servicio()

    cobro = servicio.crear_cobro(dict(ORDEN))

    assert cobro["estado"] == "cobrado"
    assert cobro["referencia"] == "ref-1"
    assert cliente_acl.llamadas == 1


def test_crear_cobro_con_pasarela_no_disponible_queda_pendiente_sin_excepcion() -> None:
    resultado = ResultadoCobro(estado="pendiente", motivo="pasarela_no_disponible")
    servicio, _, _, _ = _servicio(ClienteAclFalso([resultado]))

    cobro = servicio.crear_cobro(dict(ORDEN))

    assert cobro["estado"] == "pendiente"
    assert cobro["motivo"] == "pasarela_no_disponible"


def test_mismo_intento_recibido_dos_veces_secuencialmente_se_ejecuta_una_sola_vez() -> None:
    """CA-W05-04: un mismo intento recibido dos veces (misma `clave_idempotencia`)
    se ejecuta una sola vez — la segunda llamada NO vuelve a invocar al ACL
    Worker, devuelve la fila ya persistida."""
    servicio, repositorio, cliente_acl, _ = _servicio()

    primero = servicio.crear_cobro(dict(ORDEN))
    segundo = servicio.crear_cobro(dict(ORDEN))

    assert primero["id"] == segundo["id"]
    assert cliente_acl.llamadas == 1
    assert len(repositorio._por_id) == 1


def test_llamadas_concurrentes_con_la_misma_clave_cobran_una_sola_vez() -> None:
    """La prueba de idempotencia más importante de T-W05-3: bajo concurrencia
    real (hilos, no secuencial), dos intentos con la misma clave de
    idempotencia resultan en una sola llamada al ACL Worker y un solo cobro
    persistido — no basta con un SELECT-luego-INSERT sin atomicidad."""
    servicio, repositorio, cliente_acl, _ = _servicio()

    resultados: list[dict] = []
    lock_resultados = threading.Lock()

    def _intentar() -> None:
        cobro = servicio.crear_cobro(dict(ORDEN))
        with lock_resultados:
            resultados.append(cobro)

    hilos = [threading.Thread(target=_intentar) for _ in range(20)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert cliente_acl.llamadas == 1
    assert len(repositorio._por_id) == 1
    ids = {resultado["id"] for resultado in resultados}
    assert len(ids) == 1


def test_crear_cobro_emite_evento_al_crear_y_al_actualizar() -> None:
    servicio, _, _, bus = _servicio()

    servicio.crear_cobro(dict(ORDEN))

    estados_emitidos = [evento["datos"]["estado"] for evento in bus.eventos]
    assert estados_emitidos == ["cobrando", "cobrado"]


def test_cobro_duplicado_no_emite_eventos_nuevos() -> None:
    servicio, _, _, bus = _servicio()

    servicio.crear_cobro(dict(ORDEN))
    cantidad_tras_primero = len(bus.eventos)
    servicio.crear_cobro(dict(ORDEN))

    assert len(bus.eventos) == cantidad_tras_primero


def test_balance_poliza_suma_solo_los_cobros_en_estado_cobrado() -> None:
    repositorio = RepositorioCobrosFalso()
    servicio_cobrado = ServicioCobros(repositorio, ClienteAclFalso(), BusEventosFalso())
    servicio_pendiente = ServicioCobros(
        repositorio, ClienteAclFalso([ResultadoCobro(estado="pendiente")]), BusEventosFalso()
    )

    servicio_cobrado.crear_cobro({**ORDEN, "clave_idempotencia": "clave-1", "monto": "100.00"})
    servicio_pendiente.crear_cobro({**ORDEN, "clave_idempotencia": "clave-2", "monto": "999.00"})

    assert servicio_cobrado.balance_poliza("poliza-1") == "100.00"
