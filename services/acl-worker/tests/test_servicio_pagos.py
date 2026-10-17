import logging

from app.application.circuitos import NOMBRE_CIRCUITO_PASARELA, construir_fabrica
from app.application.servicio_pagos import ServicioCobro
from app.domain.puertos import OrdenCobro, ResultadoCobro

ORDEN = OrdenCobro(
    id_intento_cobro="idem-1",
    poliza_id="pol-1",
    monto="150000.00",
    moneda="COP",
    token_medio_pago="tok_abc123",
)


class AdaptadorFalso:
    def __init__(
        self,
        resultado: ResultadoCobro | None = None,
        error: Exception | None = None,
        registrados: dict[str, ResultadoCobro] | None = None,
    ) -> None:
        self.resultado = resultado
        self.error = error
        self.registrados = registrados or {}
        self.llamadas = 0
        self.consultas = 0

    def consultar(
        self, referencia_comercio: str, timeout_s: float | None = None
    ) -> ResultadoCobro | None:
        self.consultas += 1
        if self.error is not None:
            raise self.error
        return self.registrados.get(referencia_comercio)

    def cobrar(self, orden: OrdenCobro, timeout_s: float | None = None) -> ResultadoCobro:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.resultado


def _servicio(adaptador, umbral_fallos: int = 3, ttl_segundos: float = 10) -> ServicioCobro:
    fabrica = construir_fabrica(umbral_fallos, ttl_segundos)
    return ServicioCobro(adaptador, fabrica, NOMBRE_CIRCUITO_PASARELA, presupuesto_s=0.7)


def test_cobro_exitoso_se_devuelve_tal_cual() -> None:
    resultado = ResultadoCobro(estado="cobrado", referencia="pg-1")
    servicio = _servicio(AdaptadorFalso(resultado=resultado))

    assert servicio.cobrar(ORDEN) == resultado


def test_fallo_del_proveedor_deja_el_cobro_pendiente_sin_5xx() -> None:
    servicio = _servicio(AdaptadorFalso(error=ConnectionError("caido")))

    resultado = servicio.cobrar(ORDEN)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == "pasarela_no_disponible"


def test_circuito_abierto_tambien_deja_el_cobro_pendiente_sin_llamar_al_adaptador() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caido"))
    servicio = _servicio(adaptador, umbral_fallos=1, ttl_segundos=10)

    servicio.cobrar(ORDEN)
    llamadas_tras_primer_fallo = adaptador.llamadas

    resultado = servicio.cobrar(ORDEN)

    assert resultado.estado == "pendiente"
    assert adaptador.llamadas == llamadas_tras_primer_fallo


def test_circuito_abierto_usa_un_motivo_propio() -> None:
    servicio = _servicio(AdaptadorFalso(error=ConnectionError("caido")), umbral_fallos=1)

    primero = servicio.cobrar(ORDEN)
    segundo = servicio.cobrar(ORDEN)

    # El primero sí se intentó; el segundo ni siquiera llegó a la pasarela.
    assert primero.motivo == "pasarela_no_disponible"
    assert segundo.motivo == "circuito_abierto"


def test_consultar_encontrado() -> None:
    cobro = ResultadoCobro(estado="cobrado", referencia="pg-9")
    servicio = _servicio(AdaptadorFalso(registrados={"idem-1": cobro}))

    consulta = servicio.consultar("idem-1")

    assert consulta.resultado == "encontrado"
    assert consulta.cobro == cobro


def test_consultar_no_encontrado() -> None:
    assert _servicio(AdaptadorFalso()).consultar("no-existe").resultado == "no_encontrado"


def test_consultar_con_proveedor_caido_es_desconocido_sin_lanzar() -> None:
    servicio = _servicio(AdaptadorFalso(error=ConnectionError("caido")))

    assert servicio.consultar("idem-1").resultado == "desconocido"


def test_consultar_con_circuito_abierto_no_llama_a_la_pasarela() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caido"))
    servicio = _servicio(adaptador, umbral_fallos=1)
    servicio.cobrar(ORDEN)  # abre el circuito
    consultas_antes = adaptador.consultas

    assert servicio.consultar("idem-1").resultado == "desconocido"
    assert adaptador.consultas == consultas_antes


def test_los_logs_no_llevan_la_poliza_en_crudo(caplog) -> None:
    servicio = _servicio(AdaptadorFalso(error=ConnectionError("caido")), umbral_fallos=1)

    with caplog.at_level(logging.DEBUG):
        servicio.cobrar(ORDEN)
        servicio.cobrar(ORDEN)

    assert caplog.records
    assert ORDEN.poliza_id not in caplog.text
