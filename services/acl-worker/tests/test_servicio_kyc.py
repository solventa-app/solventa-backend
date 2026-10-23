"""KYC en el ACL (HU-W03): degradación sin 5xx, Circuit Breaker y que solo viaje el id opaco."""

import json

import httpx

from app.adapters.kyc import AdaptadorKyc
from app.application.circuitos import NOMBRE_CIRCUITO_KYC, construir_fabrica
from app.application.servicio_kyc import (
    MOTIVO_CIRCUITO_ABIERTO,
    MOTIVO_KYC_NO_DISPONIBLE,
    ServicioKyc,
)
from app.domain.puertos import ResultadoKyc, SolicitudKyc

SOLICITUD = SolicitudKyc(id_verificacion="propuesta-1", cliente_id="cliente-opaco")


class AdaptadorFalso:
    def __init__(self, resultado: ResultadoKyc | None = None, error: Exception | None = None):
        self.resultado = resultado
        self.error = error
        self.llamadas = 0

    def verificar(self, solicitud: SolicitudKyc, timeout_s: float | None = None) -> ResultadoKyc:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.resultado


def _servicio(adaptador, umbral_fallos: int = 3) -> ServicioKyc:
    return ServicioKyc(adaptador, construir_fabrica(umbral_fallos, 10), NOMBRE_CIRCUITO_KYC, 0.7)


def test_la_verificacion_exitosa_se_devuelve_tal_cual() -> None:
    resultado = ResultadoKyc(estado="aprobada", referencia="kyc-1")

    assert _servicio(AdaptadorFalso(resultado=resultado)).verificar(SOLICITUD) == resultado


def test_un_rechazo_del_proveedor_se_devuelve_tal_cual_y_no_abre_el_circuito() -> None:
    resultado = ResultadoKyc(estado="rechazada", motivo="identidad_no_coincide")
    adaptador = AdaptadorFalso(resultado=resultado)
    servicio = _servicio(adaptador, umbral_fallos=1)

    for _ in range(3):
        assert servicio.verificar(SOLICITUD).estado == "rechazada"

    assert adaptador.llamadas == 3  # un "no" del proveedor no es una falla del proveedor


def test_un_fallo_del_proveedor_deja_el_kyc_pendiente_sin_5xx() -> None:
    resultado = _servicio(AdaptadorFalso(error=ConnectionError("caído"))).verificar(SOLICITUD)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == MOTIVO_KYC_NO_DISPONIBLE


def test_con_el_circuito_abierto_no_se_llama_al_proveedor() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caído"))
    servicio = _servicio(adaptador, umbral_fallos=1)
    servicio.verificar(SOLICITUD)
    llamadas = adaptador.llamadas

    resultado = servicio.verificar(SOLICITUD)

    assert resultado.motivo == MOTIVO_CIRCUITO_ABIERTO
    assert adaptador.llamadas == llamadas  # fail-fast: el usuario no paga el timeout (EC009)


def test_los_logs_no_llevan_el_id_de_verificacion_ni_el_cliente(caplog) -> None:
    caplog.set_level("WARNING")

    _servicio(AdaptadorFalso(error=ConnectionError("caído"))).verificar(SOLICITUD)

    assert "propuesta-1" not in caplog.text
    assert "cliente-opaco" not in caplog.text


def test_el_adaptador_manda_solo_el_id_opaco_y_la_sonda_no_verifica() -> None:
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json={"estado": "aprobada", "referencia": "kyc-1"})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    adaptador = AdaptadorKyc("http://p", 0.7, cliente=cliente)

    adaptador.verificar(SOLICITUD)
    adaptador.sondear()

    assert json.loads(vistas[0].content) == {
        "referencia_comercio": "propuesta-1",
        "cliente_id": "cliente-opaco",
    }
    assert vistas[1].url.path == "/v1/estado"
    assert len(vistas) == 2
