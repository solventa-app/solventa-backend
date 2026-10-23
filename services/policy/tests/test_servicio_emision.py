"""Decisión, primer cobro y emisión (HU-W03, CA-W03-04 a 07; D-03: se cobra antes de emitir)."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
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
from app.application.servicio_emision import (
    PropuestaNoEncontradaParaEmitir,
    ServicioEmision,
    id_intento_cobro,
    numero_de_poliza,
    sumar_meses,
)
from app.application.servicio_firma import ServicioFirma
from app.application.servicio_propuestas import ServicioPropuestas

ESQUEMA = (
    Path(__file__).resolve().parents[3] / "contracts" / "events" / "poliza.emitida.schema.json"
)


def _armar(under=None, payments=None, firmar=True, max_intentos=12, notificador=None):
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    if firmar:
        ServicioFirma(repositorio, AclFalso()).firmar(propuesta["id"], True, "v1", "usuario-1")
    under = under or UnderFalso()
    payments = payments or PaymentsFalso()
    planificador, bus = PlanificadorFalso(), BusFalso()
    notificador = notificador or NotificadorFalso()
    servicio = ServicioEmision(
        repositorio, under, payments, bus, planificador, notificador, "tok_recaudo", 10.0,
        max_intentos,
    )  # fmt: skip
    servicio.notificador = notificador  # para que las pruebas lo vean
    return servicio, repositorio, under, payments, planificador, bus, propuesta["id"]


def test_aprobada_y_cobrada_emite_la_poliza_y_publica_el_evento() -> None:
    servicio, repositorio, _, payments, _, bus, propuesta_id = _armar()

    estado = servicio.emitir(propuesta_id)

    assert estado == "EMITIDA"
    propuesta = repositorio.obtener(propuesta_id)
    assert propuesta["estado"] == "EMITIDA"
    assert propuesta["poliza_id"] == "poliza-1"
    assert len(payments.ordenes) == 1
    assert [e["tipo"] for e in bus.eventos] == ["poliza.emitida"]


def test_el_evento_cumple_el_contrato() -> None:
    servicio, *_, bus, propuesta_id = _armar()

    servicio.emitir(propuesta_id)

    jsonschema.validate(bus.eventos[0], json.loads(ESQUEMA.read_text(encoding="utf-8")))
    datos = bus.eventos[0]["datos"]
    assert datos["propuestaId"] == propuesta_id
    assert datos["numeroPoliza"].startswith("SOL-")


def test_la_orden_de_cobro_sale_de_la_propuesta_y_es_siempre_la_misma() -> None:
    servicio, _, _, payments, *_, propuesta_id = _armar(payments=PaymentsFalso(["pendiente"]))

    servicio.emitir(propuesta_id)
    servicio.emitir(propuesta_id)

    primera, segunda = payments.ordenes
    assert primera == segunda  # reintentar no cobra dos veces: PAYMENTS la reconoce
    assert primera.id_intento_cobro == id_intento_cobro(propuesta_id)
    assert primera.monto == "185000"
    assert primera.moneda == "COP"
    assert primera.cliente_id == "usuario-1"  # quien firmó
    assert primera.token_medio_pago == "tok_recaudo"
    assert "tarjeta" not in repr(primera).lower()


def test_under_recibe_el_riesgo_de_la_oferta_y_la_cobertura() -> None:
    servicio, _, under, *_, propuesta_id = _armar()

    servicio.emitir(propuesta_id)

    _, cliente_id, nivel, cobertura = under.llamadas[0]
    assert cliente_id == "cliente-opaco"
    assert nivel == "BAJO"
    assert cobertura == {"valor": "200000000", "moneda": "COP"}


def test_si_under_rechaza_no_se_cobra_ni_se_emite() -> None:
    under = UnderFalso("RECHAZADA", ["KYC_RECHAZADO"])
    servicio, repositorio, _, payments, _, bus, propuesta_id = _armar(under=under)

    assert servicio.emitir(propuesta_id) == "NO_EMITIDA"

    assert payments.ordenes == []  # D-03: UNDER decide antes del cobro
    assert bus.eventos == []
    assert repositorio.obtener(propuesta_id)["motivo_estado"] == "KYC_RECHAZADO"


def test_si_under_pide_revision_no_se_cobra_ni_se_emite() -> None:
    under = UnderFalso("REVISION_MANUAL", ["RIESGO_ALTO", "COBERTURA_ALTA"])
    servicio, repositorio, _, payments, _, _, propuesta_id = _armar(under=under)

    assert servicio.emitir(propuesta_id) == "EN_REVISION"

    assert payments.ordenes == []
    assert repositorio.obtener(propuesta_id)["motivo_estado"] == "RIESGO_ALTO,COBERTURA_ALTA"


def test_si_under_no_responde_la_emision_se_reintenta_y_no_se_decide_a_ciegas() -> None:
    servicio, repositorio, _, payments, _, bus, propuesta_id = _armar(under=UnderFalso(cae=True))

    with pytest.raises(EmisionPendiente):
        servicio.emitir(propuesta_id)

    assert repositorio.obtener(propuesta_id)["estado"] == "EN_EMISION"
    assert payments.ordenes == []
    assert bus.eventos == []


def test_si_payments_no_responde_no_se_da_el_cobro_por_hecho() -> None:
    servicio, repositorio, *_, bus, propuesta_id = _armar(payments=PaymentsFalso(cae=True))

    with pytest.raises(EmisionPendiente):
        servicio.emitir(propuesta_id)

    assert repositorio.obtener(propuesta_id)["estado"] == "EN_EMISION"
    assert bus.eventos == []


def test_un_cobro_rechazado_no_emite_la_poliza() -> None:
    servicio, repositorio, _, _, _, bus, propuesta_id = _armar(
        payments=PaymentsFalso(["rechazado"])
    )

    assert servicio.emitir(propuesta_id) == "PAGO_RECHAZADO"

    assert bus.eventos == []
    assert repositorio.obtener(propuesta_id)["poliza_id"] is None
    assert repositorio.obtener(propuesta_id)["motivo_estado"] == "fondos_insuficientes"


@pytest.mark.parametrize("pendiente", ["pendiente", "cobrando"])
def test_un_cobro_pendiente_hace_esperar_la_poliza_y_se_vuelve_a_consultar(pendiente) -> None:
    servicio, repositorio, _, _, planificador, bus, propuesta_id = _armar(
        payments=PaymentsFalso([pendiente])
    )

    assert servicio.emitir(propuesta_id) == "PENDIENTE_PAGO"

    assert bus.eventos == []  # CA-W03-06: nunca emitida sin cobrar
    assert repositorio.obtener(propuesta_id)["poliza_id"] is None
    assert planificador.encolados == [(propuesta_id, 10.0)]


def test_cuando_el_cobro_pendiente_se_cobra_la_poliza_se_emite_sin_decidir_de_nuevo() -> None:
    servicio, repositorio, under, payments, _, bus, propuesta_id = _armar(
        payments=PaymentsFalso(["pendiente", "cobrado"])
    )
    servicio.emitir(propuesta_id)

    estado = servicio.emitir(propuesta_id)  # la tarea de re-consulta

    assert estado == "EMITIDA"
    assert len(under.llamadas) == 1  # UNDER no se vuelve a llamar
    assert len(payments.ordenes) == 2
    assert len(bus.eventos) == 1


def test_un_cobro_pendiente_que_no_se_cobra_deja_de_reprogramarse() -> None:
    servicio, repositorio, _, _, planificador, _, propuesta_id = _armar(
        payments=PaymentsFalso(["pendiente"]), max_intentos=3
    )

    for _ in range(6):
        servicio.emitir(propuesta_id)

    assert len(planificador.encolados) == 2  # intentos 1 y 2; el 3.º ya no reprograma
    assert repositorio.obtener(propuesta_id)["estado"] == "PENDIENTE_PAGO"


def test_emitir_dos_veces_no_duplica_la_poliza_ni_el_evento() -> None:
    servicio, _, _, payments, _, bus, propuesta_id = _armar()

    servicio.emitir(propuesta_id)
    estado = servicio.emitir(propuesta_id)

    assert estado == "EMITIDA"
    assert len(bus.eventos) == 1
    assert len(payments.ordenes) == 1  # ya emitida: ni siquiera vuelve a cobrar


def test_dos_emisiones_a_la_vez_dejan_una_sola_poliza() -> None:
    servicio, repositorio, *_, bus, propuesta_id = _armar()

    hilos = [threading.Thread(target=servicio.emitir, args=(propuesta_id,)) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(bus.eventos) == 1
    assert len(repositorio._polizas) == 1


def test_una_propuesta_sin_firmar_no_se_emite() -> None:
    servicio, _, under, payments, *_, propuesta_id = _armar(firmar=False)

    assert servicio.emitir(propuesta_id) == "ACEPTADA"

    assert under.llamadas == []
    assert payments.ordenes == []


def test_una_propuesta_desconocida_no_se_emite() -> None:
    servicio, *_ = _armar()

    with pytest.raises(PropuestaNoEncontradaParaEmitir):
        servicio.emitir("no-existe")


def test_un_conflicto_de_cobro_va_a_revision_y_no_emite() -> None:
    servicio, repositorio, *_, bus, propuesta_id = _armar(payments=PaymentsFalso(conflicto=True))

    assert servicio.emitir(propuesta_id) == "EN_REVISION"

    assert bus.eventos == []
    assert repositorio.obtener(propuesta_id)["motivo_estado"] == "COBRO_EN_CONFLICTO"


def test_sumar_meses_respeta_el_fin_de_mes() -> None:
    assert sumar_meses(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert sumar_meses(datetime(2026, 10, 22, tzinfo=UTC), 60) == datetime(2031, 10, 22, tzinfo=UTC)
    assert sumar_meses(datetime(2026, 12, 15, tzinfo=UTC), 1) == datetime(2027, 1, 15, tzinfo=UTC)


def test_el_numero_de_poliza_lleva_el_anio_y_seis_digitos() -> None:
    assert numero_de_poliza(42, datetime(2026, 10, 22, tzinfo=UTC)) == "SOL-2026-000042"


def test_los_logs_no_llevan_el_cliente_ni_la_prima(caplog) -> None:
    caplog.set_level("INFO")
    servicio, *_, propuesta_id = _armar()

    servicio.emitir(propuesta_id)

    assert "cliente-opaco" not in caplog.text
    assert "185000" not in caplog.text
    assert propuesta_id not in caplog.text


def test_al_emitir_se_avisa_al_titular_que_firmo_con_la_plantilla_y_el_enlace() -> None:
    servicio, repositorio, *_, propuesta_id = _armar()

    servicio.emitir(propuesta_id)

    (aviso,) = servicio.notificador.solicitudes
    assert aviso.plantilla == "poliza-emitida"
    assert aviso.destinatario_id == "usuario-1"  # quien firmó
    assert aviso.id_notificacion == "poliza-emitida-poliza-1"
    assert aviso.datos["numero_poliza"].startswith("SOL-")
    assert aviso.datos["enlace_documento"] == "/polizas/poliza-1/documento"
    assert repositorio._polizas[propuesta_id]["notificada_en"] is not None


def test_el_aviso_no_lleva_la_prima_ni_el_cliente_ni_el_documento() -> None:
    servicio, *_, propuesta_id = _armar()

    servicio.emitir(propuesta_id)

    texto = repr(servicio.notificador.solicitudes[0])
    assert "185000" not in texto
    assert "cliente-opaco" not in texto
    assert set(servicio.notificador.solicitudes[0].datos) == {"numero_poliza", "enlace_documento"}


def test_si_el_aviso_no_sale_la_poliza_queda_emitida_y_se_reintenta_solo_el_aviso() -> None:
    servicio, repositorio, _, payments, _, bus, propuesta_id = _armar(
        notificador=NotificadorFalso(["pendiente", "enviada"])
    )

    with pytest.raises(EmisionPendiente):
        servicio.emitir(propuesta_id)

    assert repositorio.obtener(propuesta_id)["estado"] == "EMITIDA"  # la póliza no se deshace
    assert repositorio._polizas[propuesta_id]["notificada_en"] is None
    assert len(bus.eventos) == 1

    assert servicio.emitir(propuesta_id) == "EMITIDA"  # el reintento de la tarea

    assert repositorio._polizas[propuesta_id]["notificada_en"] is not None
    assert len(bus.eventos) == 1  # el evento no se vuelve a publicar
    assert len(payments.ordenes) == 1  # ni se vuelve a cobrar


def test_una_poliza_ya_notificada_no_vuelve_a_avisar() -> None:
    servicio, *_, propuesta_id = _armar()
    servicio.emitir(propuesta_id)

    servicio.emitir(propuesta_id)
    servicio.emitir(propuesta_id)

    assert len(servicio.notificador.solicitudes) == 1


def test_emisiones_simultaneas_avisan_una_sola_vez() -> None:
    servicio, *_, propuesta_id = _armar()

    hilos = [threading.Thread(target=servicio.emitir, args=(propuesta_id,)) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(servicio.notificador.enviadas) == 1  # el proveedor es idempotente por id


def test_una_propuesta_que_no_se_emite_no_avisa() -> None:
    servicio, *_, propuesta_id = _armar(under=UnderFalso("REVISION_MANUAL", ["RIESGO_ALTO"]))

    servicio.emitir(propuesta_id)

    assert servicio.notificador.solicitudes == []
