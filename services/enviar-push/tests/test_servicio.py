import asyncio

from fakes import AuthFalso

from app.esquema_evento import construir_evento
from app.plantillas import Plantillas
from app.servicio import ServicioEnviarPush
from app.sns import EnviadorStub


def _destino() -> dict:
    return {"dispositivo_id": "d1", "token": "tok-1", "plataforma": "android"}


def test_enviado() -> None:
    auth = AuthFalso(destinos={"u1": _destino()})
    enviador = EnviadorStub()
    servicio = ServicioEnviarPush(auth, enviador, Plantillas())
    variables = {"monto": "1", "moneda": "COP"}
    evento = construir_evento("idem-1", "u1", "indemnizacion-liquidada", variables)
    resultado = asyncio.run(servicio.procesar(evento))
    assert resultado == "ENVIADO"
    assert len(enviador.enviados) == 1


def test_sin_destino() -> None:
    auth = AuthFalso(destinos={})
    servicio = ServicioEnviarPush(auth, EnviadorStub(), Plantillas())
    evento = construir_evento("idem-2", "u-sin-dispositivo", "sesion-cerrada")
    resultado = asyncio.run(servicio.procesar(evento))
    assert resultado == "SIN_DESTINO"


def test_push_deshabilitado() -> None:
    auth = AuthFalso(deshabilitados=frozenset({"u1"}))
    servicio = ServicioEnviarPush(auth, EnviadorStub(), Plantillas())
    evento = construir_evento("idem-3", "u1", "sesion-cerrada")
    resultado = asyncio.run(servicio.procesar(evento))
    assert resultado == "PUSH_DESHABILITADO"


def test_plantilla_desconocida() -> None:
    auth = AuthFalso(destinos={"u1": _destino()})
    servicio = ServicioEnviarPush(auth, EnviadorStub(), Plantillas())
    evento = construir_evento("idem-4", "u1", "no-existe")
    resultado = asyncio.run(servicio.procesar(evento))
    assert resultado == "PLANTILLA_DESCONOCIDA"


def test_token_invalido_avisa_a_auth() -> None:
    auth = AuthFalso(destinos={"u1": _destino()})
    enviador = EnviadorStub(fallos=frozenset({"tok-1"}))
    servicio = ServicioEnviarPush(auth, enviador, Plantillas())
    evento = construir_evento("idem-5", "u1", "sesion-cerrada")
    resultado = asyncio.run(servicio.procesar(evento))
    assert resultado == "TOKEN_INVALIDO"
    assert auth.tokens_invalidados == ["d1"]
