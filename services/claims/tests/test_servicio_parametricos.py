"""KAN-259 (HU-M04, Fase 4, D8): cruce de `evento.parametrico.recibido` contra las coberturas
paramétricas vigentes de POLICY y la liquidación automática del siniestro."""

from datetime import UTC, datetime

from fakes import AHORA, PaymentsFalso, PolicyParametricoFalso, RepositorioFalso

from app.application.clientes import ServicioNoDisponible
from app.application.servicio_parametricos import ServicioParametricos, siniestro_id_determinista

VIGENCIA_DESDE = datetime(2026, 1, 1, tzinfo=UTC)
VIGENCIA_HASTA = datetime(2027, 1, 1, tzinfo=UTC)


class BusEnMemoria:
    def __init__(self):
        self.eventos = []

    def publicar(self, evento):
        self.eventos.append(evento)
        return True


def _cobertura_vuelo(**cambios) -> dict:
    base = {
        "id": "cob-1",
        "poliza_id": "poliza-vuelo-1",
        "tipo": "VUELO",
        "monto_fijo": {"valor": "500000", "moneda": "COP"},
        "vigencia_desde": VIGENCIA_DESDE,
        "vigencia_hasta": VIGENCIA_HASTA,
        "estado": "VIGENTE",
        "numero_vuelo": "AV9340",
        "fecha_salida": "2026-11-11",
        "aeropuerto_origen": "BOG",
        "umbral_retraso_min": 120,
        "latitud": None,
        "longitud": None,
        "variable": None,
        "umbral": None,
        "ventana_horas": None,
    }
    base.update(cambios)
    return base


def _cobertura_clima(**cambios) -> dict:
    base = {
        "id": "cob-2",
        "poliza_id": "poliza-clima-1",
        "tipo": "CLIMA",
        "monto_fijo": {"valor": "400000", "moneda": "COP"},
        "vigencia_desde": VIGENCIA_DESDE,
        "vigencia_hasta": VIGENCIA_HASTA,
        "estado": "VIGENTE",
        "numero_vuelo": None,
        "fecha_salida": None,
        "aeropuerto_origen": None,
        "umbral_retraso_min": None,
        "latitud": 4.62,
        "longitud": -74.09,
        "variable": "PRECIPITACION_MM",
        "umbral": "50",
        "ventana_horas": 24,
    }
    base.update(cambios)
    return base


def _evento_vuelo(**cambios) -> dict:
    datos = {
        "tipo": "VUELO",
        "clave": "AV9340:2026-11-11",
        "valor": 145.0,
        "medidoEn": "2026-11-11T08:00:00+00:00",
        "fuente": "aviationstack",
        "cancelado": False,
    }
    datos.update(cambios.pop("datos", {}))
    evento = {
        "eventId": "evt-vuelo-1",
        "tipo": "evento.parametrico.recibido",
        "version": 1,
        "ocurridoEn": "2026-11-11T08:00:05+00:00",
        "datos": datos,
    }
    evento.update(cambios)
    return evento


def _evento_clima(**cambios) -> dict:
    datos = {
        "tipo": "CLIMA",
        "clave": "4.62,-74.09",
        "valor": 60.0,
        "medidoEn": "2026-11-11T08:00:00+00:00",
        "fuente": "open-meteo",
        "variable": "PRECIPITACION_MM",
    }
    datos.update(cambios.pop("datos", {}))
    evento = {
        "eventId": "evt-clima-1",
        "tipo": "evento.parametrico.recibido",
        "version": 1,
        "ocurridoEn": "2026-11-11T08:00:05+00:00",
        "datos": datos,
    }
    evento.update(cambios)
    return evento


def _servicio(policy=None, payments=None, bus=None, repo=None):
    repo = repo or RepositorioFalso()
    bus = bus if bus is not None else BusEnMemoria()
    servicio = ServicioParametricos(
        repo, policy or PolicyParametricoFalso(), payments or PaymentsFalso(), bus, lambda: AHORA
    )
    return servicio, repo, bus


def test_vuelo_sobre_umbral_crea_siniestro_y_paga():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]}
    )
    payments = PaymentsFalso()
    servicio, repo, bus = _servicio(policy=policy, payments=payments)
    resultado = servicio.procesar(_evento_vuelo())
    assert resultado == {"creados": 1, "revisadas": 1}
    sid = siniestro_id_determinista("evt-vuelo-1", "poliza-vuelo-1")
    siniestro = repo.obtener(sid, primaria=True)
    assert siniestro["estado"] == "APROBADO"
    assert siniestro["poliza_id"] == "poliza-vuelo-1"
    assert payments.ordenes[sid]["id"] == f"ind-{sid}"


def test_vuelo_bajo_umbral_no_crea_nada():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV1234", "2026-11-11"): [_cobertura_vuelo(poliza_id="poliza-vuelo-2")]}
    )
    servicio, repo, _ = _servicio(policy=policy)
    evento = _evento_vuelo(
        eventId="evt-vuelo-2", datos={"clave": "AV1234:2026-11-11", "valor": 10.0}
    )
    resultado = servicio.procesar(evento)
    assert resultado == {"creados": 0, "revisadas": 0}
    assert repo.siniestros == {}


def test_vuelo_cancelado_paga_aunque_el_retraso_sea_cero():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("LA5678", "2026-11-11"): [_cobertura_vuelo(poliza_id="poliza-vuelo-3")]}
    )
    servicio, repo, _ = _servicio(policy=policy)
    evento = _evento_vuelo(
        eventId="evt-vuelo-3",
        datos={"clave": "LA5678:2026-11-11", "valor": 0.0, "cancelado": True},
    )
    resultado = servicio.procesar(evento)
    assert resultado["creados"] == 1


def test_clima_sobre_umbral_crea_siniestro():
    policy = PolicyParametricoFalso(
        coberturas_celda={(4.62, -74.09, "PRECIPITACION_MM"): [_cobertura_clima()]}
    )
    servicio, repo, _ = _servicio(policy=policy)
    resultado = servicio.procesar(_evento_clima())
    assert resultado == {"creados": 1, "revisadas": 1}
    sid = siniestro_id_determinista("evt-clima-1", "poliza-clima-1")
    assert repo.obtener(sid, primaria=True)["estado"] == "APROBADO"


def test_clima_bajo_umbral_no_crea_nada():
    policy = PolicyParametricoFalso(
        coberturas_celda={(4.62, -74.09, "PRECIPITACION_MM"): [_cobertura_clima()]}
    )
    servicio, repo, _ = _servicio(policy=policy)
    evento = _evento_clima(datos={"valor": 5.0})
    resultado = servicio.procesar(evento)
    assert resultado == {"creados": 0, "revisadas": 0}
    assert repo.siniestros == {}


def test_evento_sin_polizas_que_califiquen_responde_sin_crear_nada():
    servicio, repo, _ = _servicio()
    resultado = servicio.procesar(_evento_vuelo())
    assert resultado == {"creados": 0, "revisadas": 0}
    assert repo.siniestros == {}


def test_cobertura_fuera_de_vigencia_no_crea_nada():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={
            ("AV9340", "2026-11-11"): [
                _cobertura_vuelo(
                    vigencia_desde=datetime(2020, 1, 1, tzinfo=UTC),
                    vigencia_hasta=datetime(2021, 1, 1, tzinfo=UTC),
                )
            ]
        }
    )
    servicio, repo, _ = _servicio(policy=policy)
    resultado = servicio.procesar(_evento_vuelo())
    assert resultado == {"creados": 0, "revisadas": 1}
    assert repo.siniestros == {}


def test_idempotencia_mismo_evento_no_duplica_pago_ni_siniestro():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]}
    )
    payments = PaymentsFalso()
    bus = BusEnMemoria()
    servicio, repo, _ = _servicio(policy=policy, payments=payments, bus=bus)
    primero = servicio.procesar(_evento_vuelo())
    segundo = servicio.procesar(_evento_vuelo())
    assert primero == {"creados": 1, "revisadas": 1}
    assert segundo == {"creados": 0, "revisadas": 1}
    assert len(repo.siniestros) == 1
    assert len(payments.ordenes) == 1
    # Solo un `siniestro.aprobado` y un `notificacion.push-solicitada`: la segunda pasada no
    # vuelve a pagar ni a avisar.
    assert len(bus.eventos) == 2


def test_publica_siniestro_aprobado_y_el_push_de_indemnizacion():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]},
        polizas={"poliza-vuelo-1": {"id": "poliza-vuelo-1", "firmante_id": "usuario-42"}},
    )
    bus = BusEnMemoria()
    servicio, repo, _ = _servicio(policy=policy, bus=bus)
    servicio.procesar(_evento_vuelo())
    tipos = [e["tipo"] for e in bus.eventos]
    assert tipos == ["siniestro.aprobado", "notificacion.push-solicitada"]
    push = bus.eventos[1]["datos"]
    assert push["usuarioId"] == "usuario-42"
    assert push["plantilla"] == "indemnizacion-liquidada"
    assert push["ruta"].startswith("/m04-02/")
    assert push["variables"] == {"monto": "500000", "moneda": "COP"}


def test_sin_poliza_para_el_push_no_revienta_nada():
    """Si POLICY falla al leer la póliza para el push, el siniestro y el pago ya quedaron hechos
    (W04-9): solo se publica `siniestro.aprobado`."""

    class PolicyQueFallaAlLeerPoliza(PolicyParametricoFalso):
        def obtener_poliza(self, poliza_id):
            raise ServicioNoDisponible("timeout")

    policy = PolicyQueFallaAlLeerPoliza(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]}
    )
    bus = BusEnMemoria()
    servicio, repo, _ = _servicio(policy=policy, bus=bus)
    resultado = servicio.procesar(_evento_vuelo())
    assert resultado["creados"] == 1
    assert len(bus.eventos) == 1
    assert bus.eventos[0]["tipo"] == "siniestro.aprobado"


def test_pago_que_falla_propaga_el_error_para_que_pubsub_reintente():
    policy = PolicyParametricoFalso(
        coberturas_vuelo={("AV9340", "2026-11-11"): [_cobertura_vuelo()]}
    )
    payments = PaymentsFalso(falla=RuntimeError("payments caído"))
    servicio, repo, _ = _servicio(policy=policy, payments=payments)
    try:
        servicio.procesar(_evento_vuelo())
        raise AssertionError("debía propagar el error")
    except RuntimeError:
        pass
    assert repo.siniestros == {}
