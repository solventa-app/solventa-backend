"""Caso de uso de clima/vuelos (KAN-259, Fase 3): degradación sin 5xx, Circuit Breaker por
proveedor y que se publique el evento correcto en el bus."""

from app.application.bus_eventos_parametricos import BusEventosLog
from app.application.circuitos import (
    NOMBRE_CIRCUITO_CLIMA,
    NOMBRE_CIRCUITO_VUELO,
    construir_fabrica,
)
from app.application.servicio_eventos_parametricos import (
    MOTIVO_CIRCUITO_ABIERTO,
    MOTIVO_PROVEEDOR_NO_DISPONIBLE,
    MOTIVO_VUELO_NO_ENCONTRADO,
    ServicioEventosParametricos,
)
from app.domain.puertos import ConsultaClima, ConsultaVuelo, MedicionClima, MedicionVuelo

CONSULTA_CLIMA = ConsultaClima(
    clave="4.61,-74.08", latitud=4.61, longitud=-74.08, variable="PRECIPITACION_MM"
)
CONSULTA_VUELO = ConsultaVuelo(numero_vuelo="AV9340", fecha="2026-11-11")


class _BusFalso:
    def __init__(self) -> None:
        self.publicados: list[dict] = []

    def publicar(self, evento: dict) -> None:
        self.publicados.append(evento)


class _AdaptadorClimaFalso:
    def __init__(self, medicion: MedicionClima | None = None, error: Exception | None = None):
        self.medicion = medicion
        self.error = error
        self.llamadas = 0

    def consultar(self, consulta: ConsultaClima, timeout_s: float | None = None) -> MedicionClima:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.medicion


class _AdaptadorVueloFalso:
    def __init__(self, medicion: MedicionVuelo | None = None, error: Exception | None = None):
        self.medicion = medicion
        self.error = error
        self.llamadas = 0

    def consultar(self, consulta: ConsultaVuelo, timeout_s: float | None = None) -> MedicionVuelo:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.medicion


def _servicio(
    adaptador_clima=None, adaptador_vuelo=None, umbral_fallos: int = 3, bus=None
) -> tuple[ServicioEventosParametricos, _BusFalso]:
    bus = bus or _BusFalso()
    fabrica = construir_fabrica(umbral_fallos, 10)
    servicio = ServicioEventosParametricos(
        adaptador_clima or _AdaptadorClimaFalso(),
        adaptador_vuelo or _AdaptadorVueloFalso(),
        fabrica,
        NOMBRE_CIRCUITO_CLIMA,
        NOMBRE_CIRCUITO_VUELO,
        0.7,
        0.7,
        bus,
    )
    return servicio, bus


def test_clima_exitoso_publica_el_evento() -> None:
    medicion = MedicionClima(valor=55.2, medido_en="2026-11-11T00:00:00+00:00")
    servicio, bus = _servicio(adaptador_clima=_AdaptadorClimaFalso(medicion=medicion))

    evento, motivo = servicio.consultar_clima(CONSULTA_CLIMA)

    assert motivo is None
    assert evento["datos"]["valor"] == 55.2
    assert evento["datos"]["variable"] == "PRECIPITACION_MM"
    assert bus.publicados == [evento]


def test_clima_con_proveedor_caido_no_publica_nada() -> None:
    adaptador = _AdaptadorClimaFalso(error=ConnectionError("caído"))
    servicio, bus = _servicio(adaptador_clima=adaptador)

    evento, motivo = servicio.consultar_clima(CONSULTA_CLIMA)

    assert evento is None
    assert motivo == MOTIVO_PROVEEDOR_NO_DISPONIBLE
    assert bus.publicados == []


def test_clima_con_el_circuito_abierto_no_llama_al_proveedor() -> None:
    adaptador = _AdaptadorClimaFalso(error=ConnectionError("caído"))
    servicio, _ = _servicio(adaptador_clima=adaptador, umbral_fallos=1)
    servicio.consultar_clima(CONSULTA_CLIMA)
    llamadas = adaptador.llamadas

    _, motivo = servicio.consultar_clima(CONSULTA_CLIMA)

    assert motivo == MOTIVO_CIRCUITO_ABIERTO
    assert adaptador.llamadas == llamadas


def test_vuelo_exitoso_publica_el_evento() -> None:
    medicion = MedicionVuelo(
        encontrado=True, retraso_minutos=145, cancelado=False, medido_en="2026-11-11T00:00:00+00:00"
    )
    servicio, bus = _servicio(adaptador_vuelo=_AdaptadorVueloFalso(medicion=medicion))

    evento, motivo = servicio.consultar_vuelo(CONSULTA_VUELO)

    assert motivo is None
    assert evento["datos"]["clave"] == "AV9340:2026-11-11"
    assert evento["datos"]["valor"] == 145.0
    assert bus.publicados == [evento]


def test_vuelo_no_encontrado_no_publica_nada_y_no_es_una_falla() -> None:
    medicion = MedicionVuelo(encontrado=False, medido_en="2026-11-11T00:00:00+00:00")
    adaptador = _AdaptadorVueloFalso(medicion=medicion)
    servicio, bus = _servicio(adaptador_vuelo=adaptador, umbral_fallos=1)

    for _ in range(3):
        evento, motivo = servicio.consultar_vuelo(CONSULTA_VUELO)
        assert evento is None
        assert motivo == MOTIVO_VUELO_NO_ENCONTRADO

    assert adaptador.llamadas == 3  # no encontrado no abre el circuito
    assert bus.publicados == []


def test_vuelo_con_proveedor_caido_no_publica_nada() -> None:
    adaptador = _AdaptadorVueloFalso(error=ConnectionError("caído"))
    servicio, bus = _servicio(adaptador_vuelo=adaptador)

    evento, motivo = servicio.consultar_vuelo(CONSULTA_VUELO)

    assert evento is None
    assert motivo == MOTIVO_PROVEEDOR_NO_DISPONIBLE
    assert bus.publicados == []


def test_el_bus_por_defecto_es_el_log_sin_romper_nada() -> None:
    medicion = MedicionClima(valor=1.0, medido_en="2026-11-11T00:00:00+00:00")
    servicio = ServicioEventosParametricos(
        _AdaptadorClimaFalso(medicion=medicion),
        _AdaptadorVueloFalso(),
        construir_fabrica(3, 10),
        NOMBRE_CIRCUITO_CLIMA,
        NOMBRE_CIRCUITO_VUELO,
        0.7,
        0.7,
        BusEventosLog(),
    )
    evento, motivo = servicio.consultar_clima(CONSULTA_CLIMA)
    assert motivo is None
    assert evento is not None
