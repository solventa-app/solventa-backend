from app.application.bus_eventos_parametricos import (
    BusEventosLog,
    BusEventosPubSub,
    construir_bus_eventos_parametricos,
)


def _evento() -> dict:
    return {
        "eventId": "e1",
        "tipo": "evento.parametrico.recibido",
        "version": 1,
        "ocurridoEn": "2026-11-11T00:00:00+00:00",
        "datos": {
            "tipo": "CLIMA",
            "clave": "4.61,-74.08",
            "valor": 55.2,
            "medidoEn": "2026-11-11T00:00:00+00:00",
            "fuente": "open-meteo",
            "variable": "PRECIPITACION_MM",
        },
    }


def test_construir_bus_sin_tema_da_el_log() -> None:
    assert isinstance(construir_bus_eventos_parametricos(""), BusEventosLog)
    assert isinstance(construir_bus_eventos_parametricos(None), BusEventosLog)


def test_construir_bus_con_tema_da_pubsub() -> None:
    assert isinstance(
        construir_bus_eventos_parametricos("proyectos/x/topics/eventos-parametricos"),
        BusEventosPubSub,
    )


def test_log_no_falla_al_publicar() -> None:
    BusEventosLog().publicar(_evento())


class _FuturoFalso:
    def result(self, timeout: float) -> None:
        return None


class _ClientePubSubFalso:
    def __init__(self) -> None:
        self.publicados: list[tuple[str, bytes]] = []

    def publish(self, tema: str, cuerpo: bytes, tipo: str | None = None) -> _FuturoFalso:
        self.publicados.append((tema, cuerpo))
        return _FuturoFalso()


class _ClientePubSubQueFalla:
    def publish(self, tema: str, cuerpo: bytes, tipo: str | None = None) -> None:
        raise RuntimeError("tema inexistente")


def test_pubsub_publica_en_el_tema() -> None:
    cliente = _ClientePubSubFalso()
    bus = BusEventosPubSub("proyectos/x/topics/eventos-parametricos", cliente_pubsub=cliente)
    bus.publicar(_evento())
    assert len(cliente.publicados) == 1
    assert cliente.publicados[0][0] == "proyectos/x/topics/eventos-parametricos"


def test_pubsub_se_degrada_al_log_si_falla(caplog) -> None:
    bus = BusEventosPubSub(
        "proyectos/x/topics/eventos-parametricos", cliente_pubsub=_ClientePubSubQueFalla()
    )
    bus.publicar(_evento())  # no debe lanzar
