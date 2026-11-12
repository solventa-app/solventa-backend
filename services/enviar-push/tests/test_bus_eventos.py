from app.bus_eventos import BusEventosLog, BusEventosPubSub, construir_bus_eventos


def _evento() -> dict:
    return {
        "eventId": "e1",
        "tipo": "notificacion.push-solicitada",
        "version": 1,
        "ocurridoEn": "2026-11-11T00:00:00+00:00",
        "datos": {"idempotencia": "i1", "usuarioId": "u1", "plantilla": "indemnizacion-liquidada"},
    }


def test_construir_bus_sin_tema_da_el_log() -> None:
    assert isinstance(construir_bus_eventos(""), BusEventosLog)
    assert isinstance(construir_bus_eventos(None), BusEventosLog)


def test_construir_bus_con_tema_da_pubsub() -> None:
    assert isinstance(construir_bus_eventos("proyectos/x/topics/y"), BusEventosPubSub)


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
    bus = BusEventosPubSub("proyectos/x/topics/notificaciones-push", cliente_pubsub=cliente)
    bus.publicar(_evento())
    assert len(cliente.publicados) == 1
    assert cliente.publicados[0][0] == "proyectos/x/topics/notificaciones-push"


def test_pubsub_se_degrada_al_log_si_falla(caplog) -> None:
    bus = BusEventosPubSub(
        "proyectos/x/topics/notificaciones-push", cliente_pubsub=_ClientePubSubQueFalla()
    )
    bus.publicar(_evento())  # no debe lanzar
