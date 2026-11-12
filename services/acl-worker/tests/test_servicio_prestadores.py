"""Red de prestadores en el ACL (HU-M06, v0.46, B7): degradación sin 5xx y Circuit Breaker."""

import json

import httpx

from app.adapters.prestadores import AdaptadorPrestadores
from app.application.circuitos import NOMBRE_CIRCUITO_PRESTADORES, construir_fabrica
from app.application.servicio_prestadores import MOTIVO_SIN_RESPUESTA, ServicioPrestadores
from app.domain.puertos import DesenlaceAsistencia, PedidoAsistencia

PEDIDO = PedidoAsistencia(
    asistencia_id="asistencia-secreta",
    intento=1,
    tipo_asistencia="MEDICO",
    prestador_id="prs-bog-001",
    prestador_latitud=4.6859,
    prestador_longitud=-74.0524,
    latitud=4.6760,
    longitud=-74.0480,
)


class AdaptadorFalso:
    def __init__(self, desenlace=None, error=None):
        self.desenlace = desenlace
        self.error = error
        self.llamadas = 0

    def solicitar(self, pedido, timeout_s=None):
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.desenlace


def _servicio(adaptador, umbral_fallos=3):
    return ServicioPrestadores(
        adaptador, construir_fabrica(umbral_fallos, 10), NOMBRE_CIRCUITO_PRESTADORES, 0.7
    )


def test_el_desenlace_del_proveedor_se_devuelve_tal_cual():
    confirmada = DesenlaceAsistencia(estado="CONFIRMADA", minutos_estimados=6)
    assert _servicio(AdaptadorFalso(confirmada)).solicitar(PEDIDO) == confirmada


def test_un_no_del_proveedor_no_abre_el_circuito():
    no = DesenlaceAsistencia(estado="NO_CONFIRMADA", motivo=MOTIVO_SIN_RESPUESTA)
    adaptador = AdaptadorFalso(no)
    servicio = _servicio(adaptador, umbral_fallos=1)
    for _ in range(3):
        servicio.solicitar(PEDIDO)
    assert adaptador.llamadas == 3


def test_si_el_proveedor_falla_queda_no_confirmada_y_luego_falla_rapido():
    adaptador = AdaptadorFalso(error=ConnectionError("caído"))
    servicio = _servicio(adaptador, umbral_fallos=1)
    assert servicio.solicitar(PEDIDO) == DesenlaceAsistencia(
        estado="NO_CONFIRMADA", motivo=MOTIVO_SIN_RESPUESTA
    )
    llamadas = adaptador.llamadas
    assert servicio.solicitar(PEDIDO).estado == "NO_CONFIRMADA"
    assert adaptador.llamadas == llamadas  # circuito abierto: no se llama al proveedor


def test_los_logs_no_llevan_el_id_ni_la_ubicacion(caplog):
    caplog.set_level("WARNING")
    _servicio(AdaptadorFalso(error=ConnectionError("caído"))).solicitar(PEDIDO)
    assert "asistencia-secreta" not in caplog.text
    assert "4.676" not in caplog.text


def test_el_adaptador_traduce_a_http():
    enviados = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        enviados.append((peticion.url.path, json.loads(peticion.content or b"null")))
        if peticion.url.path == "/v1/estado":
            return httpx.Response(200, json={"estado": "ok"})
        return httpx.Response(
            200, json={"estado": "CONFIRMADA", "minutos_estimados": 6, "motivo": None}
        )

    cliente = httpx.Client(base_url="http://prestadores", transport=httpx.MockTransport(responder))
    adaptador = AdaptadorPrestadores("http://prestadores", 0.7, cliente=cliente)

    assert adaptador.solicitar(PEDIDO) == DesenlaceAsistencia(
        estado="CONFIRMADA", minutos_estimados=6
    )
    adaptador.sondear()
    ruta, cuerpo = enviados[0]
    assert ruta == "/v1/asistencias"
    assert cuerpo["asistencia_id"] == "asistencia-secreta"
    assert cuerpo["latitud"] == 4.6760
    assert enviados[1][0] == "/v1/estado"
    adaptador.cerrar()
