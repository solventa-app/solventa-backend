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


def test_con_nombre_en_auth_el_titulo_saluda_y_el_cuerpo_dice_el_evento() -> None:
    auth = AuthFalso(destinos={"u1": {**_destino(), "primer_nombre": "Felipe"}})
    enviador = EnviadorStub()
    servicio = ServicioEnviarPush(auth, enviador, Plantillas())
    variables = {"monto": "500000.00", "moneda": "COP", "vuelo": "AV9340", "minutos": "145.0"}
    evento = construir_evento("idem-9", "u1", "indemnizacion-vuelo-retraso", variables)

    assert asyncio.run(servicio.procesar(evento)) == "ENVIADO"

    mensaje = enviador.enviados[0]["mensaje"]
    assert mensaje["titulo"] == "Felipe, tu indemnización ya está lista"
    assert mensaje["cuerpo"] == (
        "Se liquidaron $ 500.000 por el retraso de 145 min de tu vuelo AV9340. "
        "Toca para ver el detalle."
    )


def test_sin_nombre_el_titulo_no_saluda_y_un_nombre_del_productor_se_ignora() -> None:
    auth = AuthFalso(destinos={"u1": _destino()})
    enviador = EnviadorStub()
    servicio = ServicioEnviarPush(auth, enviador, Plantillas())
    variables = {"monto": "400.000", "moneda": "COP", "valor": "62", "nombre": "Intruso"}
    evento = construir_evento("idem-10", "u1", "indemnizacion-clima-lluvia", variables)

    asyncio.run(servicio.procesar(evento))

    mensaje = enviador.enviados[0]["mensaje"]
    assert mensaje["titulo"] == "Tu indemnización ya está lista"
    assert "62 mm" in mensaje["cuerpo"] and "Intruso" not in str(mensaje)


def test_el_push_sale_en_el_idioma_que_el_cliente_eligio() -> None:
    auth = AuthFalso(destinos={"u1": {**_destino(), "primer_nombre": "Felipe", "idioma": "en"}})
    enviador = EnviadorStub()
    servicio = ServicioEnviarPush(auth, enviador, Plantillas())
    variables = {"monto": "500000.00", "moneda": "COP", "vuelo": "AV9340", "minutos": "145.0"}
    evento = construir_evento("idem-10", "u1", "indemnizacion-vuelo-retraso", variables)

    assert asyncio.run(servicio.procesar(evento)) == "ENVIADO"

    mensaje = enviador.enviados[0]["mensaje"]
    assert mensaje["titulo"] == "Felipe, your payout is ready"
    assert mensaje["cuerpo"] == (
        "COP 500,000 was paid out for the 145-minute delay of your flight AV9340. "
        "Tap to see the details."
    )


class TasasFalsas:
    def __init__(self, tabla: dict[str, str] | None = None, falla: bool = False) -> None:
        self._tabla = tabla or {"COP": "1", "USD": "4100", "CLP": "4.35"}
        self._falla = falla
        self.consultas = 0

    async def tasas_cambio(self) -> dict[str, str]:
        from app.clientes import ErrorServicio

        self.consultas += 1
        if self._falla:
            raise ErrorServicio("HTTP 503")
        return self._tabla


def _cuerpo_con(destino: dict, tasas: TasasFalsas | None, variables: dict) -> str:
    auth = AuthFalso(destinos={"u1": {**_destino(), **destino}})
    enviador = EnviadorStub()
    servicio = ServicioEnviarPush(auth, enviador, Plantillas(), tasas)
    evento = construir_evento("idem-m", "u1", "indemnizacion-liquidada", variables)
    assert asyncio.run(servicio.procesar(evento)) == "ENVIADO"
    return enviador.enviados[0]["mensaje"]["cuerpo"]


def test_el_monto_sale_en_la_moneda_que_el_cliente_eligio() -> None:
    tasas = TasasFalsas()
    cuerpo = _cuerpo_con(
        {"moneda": "USD", "idioma": "en"}, tasas, {"monto": "500000.00", "moneda": "COP"}
    )
    # 500.000 COP / 4.100 = 121,95 USD
    assert cuerpo.startswith("USD 121.95 was paid out")
    assert tasas.consultas == 1


def test_en_espanol_el_monto_convertido_usa_coma_decimal() -> None:
    cuerpo = _cuerpo_con(
        {"moneda": "USD", "idioma": "es"}, TasasFalsas(), {"monto": "500000", "moneda": "COP"}
    )
    assert cuerpo.startswith("Se liquidaron USD 121,95 ")


def test_con_la_misma_moneda_no_se_consultan_tasas() -> None:
    tasas = TasasFalsas()
    cuerpo = _cuerpo_con({"moneda": "COP"}, tasas, {"monto": "500000.00", "moneda": "COP"})
    assert cuerpo.startswith("Se liquidaron $ 500.000 ")
    assert tasas.consultas == 0


def test_sin_tasas_el_push_sale_en_la_moneda_original() -> None:
    variables = {"monto": "500000.00", "moneda": "COP"}
    for tasas in (TasasFalsas(falla=True), TasasFalsas(tabla={"COP": "1"}), None):
        cuerpo = _cuerpo_con({"moneda": "USD"}, tasas, variables)
        assert cuerpo.startswith("Se liquidaron $ 500.000 ")


def test_los_numeros_del_evento_se_escriben_en_el_idioma_del_cliente() -> None:
    from app.monedas import numero_legible

    assert numero_legible("1464.5", "es") == "1.464,5"
    assert numero_legible("1464.5", "en") == "1,464.5"
    assert numero_legible("145.0", "es") == "145"
    assert numero_legible("114942.53", "es", 0) == "114.943"
    assert numero_legible("no-es-numero", "es") == "no-es-numero"
