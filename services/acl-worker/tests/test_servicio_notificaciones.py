"""Notificaciones en el ACL (HU-W03, bloque 5): degradación sin 5xx, circuito y datos que viajan."""

import json

import httpx

from app.adapters.notificaciones import AdaptadorNotificaciones
from app.application.circuitos import NOMBRE_CIRCUITO_NOTIFICACIONES, construir_fabrica
from app.application.servicio_notificaciones import (
    MOTIVO_CIRCUITO_ABIERTO,
    MOTIVO_NOTIFICACIONES_NO_DISPONIBLES,
    ServicioNotificaciones,
)
from app.domain.puertos import ResultadoNotificacion, SolicitudNotificacion

SOLICITUD = SolicitudNotificacion(
    id_notificacion="poliza-emitida-pz-1",
    plantilla="poliza-emitida",
    destinatario_id="usuario-1",
    datos={"numero_poliza": "SOL-2026-000001", "enlace_documento": "/polizas/pz-1/documento"},
)


class AdaptadorFalso:
    def __init__(self, resultado: ResultadoNotificacion | None = None, error=None):
        self.resultado = resultado
        self.error = error
        self.llamadas = 0

    def enviar(self, solicitud, timeout_s=None) -> ResultadoNotificacion:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.resultado


def _servicio(adaptador, umbral_fallos: int = 3) -> ServicioNotificaciones:
    return ServicioNotificaciones(
        adaptador, construir_fabrica(umbral_fallos, 10), NOMBRE_CIRCUITO_NOTIFICACIONES, 0.7
    )


def test_el_envio_exitoso_se_devuelve_tal_cual() -> None:
    resultado = ResultadoNotificacion(estado="enviada", referencia="nt-1")

    assert _servicio(AdaptadorFalso(resultado)).enviar(SOLICITUD) == resultado


def test_un_fallo_del_proveedor_deja_el_aviso_pendiente_sin_5xx() -> None:
    resultado = _servicio(AdaptadorFalso(error=ConnectionError("caído"))).enviar(SOLICITUD)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == MOTIVO_NOTIFICACIONES_NO_DISPONIBLES


def test_con_el_circuito_abierto_no_se_llama_al_proveedor() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caído"))
    servicio = _servicio(adaptador, umbral_fallos=1)
    servicio.enviar(SOLICITUD)
    llamadas = adaptador.llamadas

    resultado = servicio.enviar(SOLICITUD)

    assert resultado.motivo == MOTIVO_CIRCUITO_ABIERTO
    assert adaptador.llamadas == llamadas  # fail-fast: el usuario no paga el timeout (EC009)


def test_los_logs_no_llevan_el_id_del_aviso_ni_el_destinatario(caplog) -> None:
    caplog.set_level("WARNING")

    _servicio(AdaptadorFalso(error=ConnectionError("caído"))).enviar(SOLICITUD)

    assert "poliza-emitida-pz-1" not in caplog.text
    assert "usuario-1" not in caplog.text


def test_el_adaptador_manda_la_plantilla_el_id_opaco_y_los_datos_nada_mas() -> None:
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json={"estado": "enviada", "referencia": "nt-1"})

    cliente = httpx.Client(base_url="http://p", transport=httpx.MockTransport(handler))
    adaptador = AdaptadorNotificaciones("http://p", 0.7, cliente=cliente)

    adaptador.enviar(SOLICITUD)
    adaptador.sondear()

    assert json.loads(vistas[0].content) == {
        "referencia_comercio": "poliza-emitida-pz-1",
        "plantilla": "poliza-emitida",
        "destinatario_id": "usuario-1",
        "datos": {
            "numero_poliza": "SOL-2026-000001",
            "enlace_documento": "/polizas/pz-1/documento",
        },
    }
    assert vistas[1].url.path == "/v1/estado"
    assert len(vistas) == 2  # la sonda no envía nada
