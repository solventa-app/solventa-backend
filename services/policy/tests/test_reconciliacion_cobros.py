"""Reabrir una póliza cuyo primer cobro se recuperó después (CA-W03-06, decisión W03-14).

Solo la reconciliación reabre: el back-office (tras reintentar un cobro) y el barrido. La emisión
normal nunca emite un `PAGO_RECHAZADO`."""

import pytest
from fakes import (
    AclFalso,
    BusFalso,
    NotificadorFalso,
    PaymentsFalso,
    PlanificadorFalso,
    RatingFalso,
    RepositorioPropuestasFalso,
    UnderFalso,
)

from app.application.planificador import EmisionPendiente
from app.application.servicio_emision import PropuestaNoEncontradaParaEmitir, ServicioEmision
from app.application.servicio_firma import ServicioFirma
from app.application.servicio_propuestas import ServicioPropuestas


def _armar(payments: PaymentsFalso, notificador=None, lote=100):
    """Una propuesta firmada y con el primer cobro ya intentado una vez (la emisión normal)."""
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    ServicioFirma(repositorio, AclFalso()).firmar(propuesta["id"], True, "v1", "usuario-1")
    planificador, bus = PlanificadorFalso(), BusFalso()
    notificador = notificador or NotificadorFalso()
    servicio = ServicioEmision(
        repositorio, UnderFalso(), payments, bus, planificador, notificador, "tok_recaudo", 10.0,
        12, lote, 30,
    )  # fmt: skip
    return servicio, repositorio, planificador, bus, notificador, propuesta["id"]


def test_un_pago_rechazado_que_se_recupera_emite_la_poliza() -> None:
    # El primer intento lo rechaza la pasarela; el back-office lo reintenta y PAYMENTS lo cobra.
    payments = PaymentsFalso(["rechazado", "cobrado"])
    servicio, repositorio, _, bus, notificador, propuesta_id = _armar(payments)
    assert servicio.emitir(propuesta_id) == "PAGO_RECHAZADO"

    estado = servicio.reconciliar_cobro(propuesta_id)

    assert estado == "EMITIDA"
    propuesta = repositorio.obtener(propuesta_id)
    assert propuesta["estado"] == "EMITIDA"
    assert propuesta["poliza_id"] is not None
    assert [e["tipo"] for e in bus.eventos] == ["poliza.emitida"]
    assert len(notificador.enviadas) == 1


def test_consultar_el_cobro_no_cobra_de_nuevo() -> None:
    # La orden es la misma de siempre: PAYMENTS devuelve el cobro que existe, no crea otro.
    payments = PaymentsFalso(["rechazado", "cobrado"])
    servicio, _, _, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)

    servicio.reconciliar_cobro(propuesta_id)

    assert len({orden.id_intento_cobro for orden in payments.ordenes}) == 1


def test_reconciliar_dos_veces_emite_una_sola_poliza() -> None:
    payments = PaymentsFalso(["rechazado", "cobrado"])
    servicio, repositorio, _, bus, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)

    primera = servicio.reconciliar_cobro(propuesta_id)
    segunda = servicio.reconciliar_cobro(propuesta_id)

    assert primera == segunda == "EMITIDA"
    assert len(bus.eventos) == 1
    assert repositorio.obtener(propuesta_id)["poliza_id"] == "poliza-1"


def test_un_pago_que_sigue_rechazado_no_se_emite() -> None:
    payments = PaymentsFalso(["rechazado"])
    servicio, repositorio, planificador, bus, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)

    estado = servicio.reconciliar_cobro(propuesta_id)

    assert estado == "PAGO_RECHAZADO"
    assert repositorio.obtener(propuesta_id)["poliza_id"] is None
    assert bus.eventos == []
    assert planificador.encolados == []


def test_la_emision_normal_no_reabre_un_pago_rechazado() -> None:
    # Aunque el cobro se haya recuperado, solo la reconciliación reabre: `emitir` no lo hace.
    payments = PaymentsFalso(["rechazado", "cobrado"])
    servicio, repositorio, _, bus, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)

    assert servicio.emitir(propuesta_id) == "PAGO_RECHAZADO"

    assert repositorio.obtener(propuesta_id)["estado"] == "PAGO_RECHAZADO"
    assert bus.eventos == []


def test_un_rechazado_cuyo_cobro_se_esta_reintentando_vuelve_a_pendiente_y_se_reconsulta() -> None:
    payments = PaymentsFalso(["rechazado", "pendiente"])
    servicio, repositorio, planificador, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)

    estado = servicio.reconciliar_cobro(propuesta_id)

    assert estado == "PENDIENTE_PAGO"
    propuesta = repositorio.obtener(propuesta_id)
    assert propuesta["estado"] == "PENDIENTE_PAGO"
    assert propuesta["intentos_emision"] == 0  # otra tanda completa de consultas
    assert planificador.encolados == [(propuesta_id, 10.0)]


def test_un_pendiente_agotado_que_por_fin_se_cobra_se_emite() -> None:
    payments = PaymentsFalso(["pendiente", "cobrado"])
    servicio, repositorio, _, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)
    repositorio._propuesta(propuesta_id)["intentos_emision"] = 99  # ya no se consulta solo

    estado = servicio.reconciliar_cobro(propuesta_id)

    assert estado == "EMITIDA"


@pytest.mark.parametrize("estado", ["ACEPTADA", "NO_EMITIDA", "EN_REVISION"])
def test_los_demas_estados_no_se_tocan(estado: str) -> None:
    payments = PaymentsFalso(["cobrado"])
    servicio, repositorio, _, _, _, propuesta_id = _armar(payments)
    repositorio._propuesta(propuesta_id)["estado"] = estado

    assert servicio.reconciliar_cobro(propuesta_id) == estado

    assert payments.ordenes == []  # ni siquiera se consulta a PAYMENTS


def test_sin_firma_no_se_emite_nunca() -> None:
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    repositorio._propuesta(propuesta["id"])["estado"] = "PAGO_RECHAZADO"
    servicio = ServicioEmision(
        repositorio, UnderFalso(), PaymentsFalso(["cobrado"]), BusFalso(), PlanificadorFalso(),
        NotificadorFalso(), "tok",
    )  # fmt: skip

    assert servicio.reconciliar_cobro(propuesta["id"]) == "PAGO_RECHAZADO"


def test_payments_caido_se_avisa_y_la_propuesta_no_cambia() -> None:
    payments = PaymentsFalso(["rechazado"])
    servicio, repositorio, _, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)
    payments.cae = True

    with pytest.raises(EmisionPendiente):
        servicio.reconciliar_cobro(propuesta_id)

    assert repositorio.obtener(propuesta_id)["estado"] == "PAGO_RECHAZADO"


def test_una_propuesta_que_no_existe() -> None:
    servicio, *_ = _armar(PaymentsFalso())

    with pytest.raises(PropuestaNoEncontradaParaEmitir):
        servicio.reconciliar_cobro("no-existe")


def test_si_el_aviso_falla_la_poliza_queda_emitida_y_se_deja_una_tarea() -> None:
    payments = PaymentsFalso(["rechazado", "cobrado"])
    notificador = NotificadorFalso(["pendiente"])
    servicio, repositorio, planificador, _, _, propuesta_id = _armar(payments, notificador)
    servicio.emitir(propuesta_id)

    estado = servicio.reconciliar_cobro(propuesta_id)

    assert estado == "EMITIDA"
    assert repositorio.obtener(propuesta_id)["estado"] == "EMITIDA"
    assert planificador.encolados == [(propuesta_id, 10.0)]


# ---------------------------------------------------------------- el barrido


def test_el_barrido_emite_las_que_ya_se_cobraron_y_cuenta_el_resto() -> None:
    payments = PaymentsFalso(["rechazado", "cobrado"])
    servicio, _, _, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)

    resumen = servicio.reconciliar_pendientes()

    assert resumen == {
        "revisadas": 1,
        "emitidas": 1,
        "rechazadas": 0,
        "pendientes": 0,
        "fallidas": 0,
    }


def test_el_barrido_no_renueva_una_propuesta_que_sigue_igual() -> None:
    # Si cada pasada la tocara, nunca saldría de la ventana de días de la reconciliación.
    payments = PaymentsFalso(["rechazado"])
    servicio, repositorio, _, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)
    cambios: list[str] = []
    original = repositorio.cambiar_estado
    repositorio.cambiar_estado = lambda *a, **k: cambios.append(a[1]) or original(*a, **k)

    resumen = servicio.reconciliar_pendientes()

    assert resumen["rechazadas"] == 1
    assert cambios == []


def test_el_barrido_sigue_aunque_una_falle() -> None:
    payments = PaymentsFalso(["rechazado"])
    servicio, _, _, _, _, propuesta_id = _armar(payments)
    servicio.emitir(propuesta_id)
    payments.cae = True

    resumen = servicio.reconciliar_pendientes()

    assert resumen["fallidas"] == 1
    assert resumen["revisadas"] == 0


def test_el_barrido_respeta_el_tamano_del_lote() -> None:
    repositorio = RepositorioPropuestasFalso()
    rating = RatingFalso()
    servicio = ServicioEmision(
        repositorio, UnderFalso(), PaymentsFalso(["rechazado"]), BusFalso(), PlanificadorFalso(),
        NotificadorFalso(), "tok", 10.0, 12, 1, 30,
    )  # fmt: skip
    for n in range(3):
        oferta = f"oferta-{n}"
        rating.ofertas[oferta] = rating.ofertas["oferta-1"] | {"id": oferta}
        propuesta = ServicioPropuestas(repositorio, rating).aceptar(oferta, "u")
        ServicioFirma(repositorio, AclFalso()).firmar(propuesta["id"], True, "v1", "u")
        servicio.emitir(propuesta["id"])

    resumen = servicio.reconciliar_pendientes()

    assert resumen["revisadas"] == 1
