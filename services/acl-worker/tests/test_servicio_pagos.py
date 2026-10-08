from app.application.circuitos import NOMBRE_CIRCUITO_PASARELA, construir_fabrica
from app.application.servicio_pagos import ServicioCobro
from app.domain.puertos import OrdenCobro, ResultadoCobro

ORDEN = OrdenCobro(
    clave_idempotencia="idem-1",
    poliza_id="pol-1",
    monto="150000.00",
    moneda="COP",
    token_medio_pago="tok_abc123",
)


class AdaptadorFalso:
    def __init__(
        self, resultado: ResultadoCobro | None = None, error: Exception | None = None
    ) -> None:
        self.resultado = resultado
        self.error = error
        self.llamadas = 0

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
