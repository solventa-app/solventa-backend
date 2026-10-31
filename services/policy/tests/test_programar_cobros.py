"""W05-A7: con la póliza emitida, la tarea pide a PAYMENTS el cobro recurrente."""

from datetime import UTC, datetime

import pytest
from fakes import PaymentsFalso

from app.application.planificador import EmisionPendiente
from app.application.servicio_emision import ServicioEmision

VIGENCIA = datetime(2027, 11, 1, tzinfo=UTC)
PROPUESTA = {"id": "propuesta-1", "poliza_id": "poliza-1"}


class _Repositorio:
    def __init__(self, poliza: dict | None) -> None:
        self.poliza = poliza

    def obtener_poliza(self, poliza_id: str) -> dict | None:
        return self.poliza


def _servicio(payments: PaymentsFalso, poliza: dict | None) -> ServicioEmision:
    servicio = ServicioEmision.__new__(ServicioEmision)
    servicio._repositorio = _Repositorio(poliza)
    servicio._payments = payments
    return servicio


def test_pide_la_programacion_con_la_vigencia_de_la_poliza() -> None:
    payments = PaymentsFalso()

    _servicio(payments, {"id": "poliza-1", "vigencia_hasta": VIGENCIA})._programar_cobros(PROPUESTA)

    assert payments.programadas == [("propuesta-1", "poliza-1", VIGENCIA.isoformat())]


def test_payments_caido_deja_la_tarea_para_reintentar() -> None:
    servicio = _servicio(PaymentsFalso(cae=True), {"id": "poliza-1", "vigencia_hasta": VIGENCIA})

    with pytest.raises(EmisionPendiente):
        servicio._programar_cobros(PROPUESTA)


def test_sin_poliza_no_pide_nada() -> None:
    payments = PaymentsFalso()

    _servicio(payments, None)._programar_cobros(PROPUESTA)

    assert payments.programadas == []
