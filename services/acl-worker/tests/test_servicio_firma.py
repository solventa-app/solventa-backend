"""Firma electrónica en el ACL (HU-W03, CA-W03-03): degradación sin 5xx y Circuit Breaker."""

from app.application.circuitos import NOMBRE_CIRCUITO_FIRMA, construir_fabrica
from app.application.servicio_firma import (
    MOTIVO_CIRCUITO_ABIERTO,
    MOTIVO_FIRMA_NO_DISPONIBLE,
    ServicioFirma,
)
from app.domain.puertos import ResultadoFirma, SolicitudFirma

SOLICITUD = SolicitudFirma(
    id_firma="propuesta-1",
    documento_hash="a" * 64,
    version_documento="v1",
    firmante_id="usuario-1",
)


class AdaptadorFalso:
    def __init__(self, resultado: ResultadoFirma | None = None, error: Exception | None = None):
        self.resultado = resultado
        self.error = error
        self.llamadas = 0

    def firmar(self, solicitud: SolicitudFirma, timeout_s: float | None = None) -> ResultadoFirma:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        return self.resultado


def _servicio(adaptador, umbral_fallos: int = 3, ttl_segundos: float = 10) -> ServicioFirma:
    fabrica = construir_fabrica(umbral_fallos, ttl_segundos)
    return ServicioFirma(adaptador, fabrica, NOMBRE_CIRCUITO_FIRMA, presupuesto_s=0.7)


def test_la_firma_exitosa_se_devuelve_tal_cual() -> None:
    resultado = ResultadoFirma(
        estado="firmada", referencia="fe-1", firmada_en="2026-10-22T12:00:00Z"
    )

    assert _servicio(AdaptadorFalso(resultado=resultado)).firmar(SOLICITUD) == resultado


def test_un_fallo_del_proveedor_deja_la_firma_pendiente_sin_5xx() -> None:
    resultado = _servicio(AdaptadorFalso(error=ConnectionError("caído"))).firmar(SOLICITUD)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == MOTIVO_FIRMA_NO_DISPONIBLE


def test_con_el_circuito_abierto_no_se_llama_al_proveedor() -> None:
    adaptador = AdaptadorFalso(error=ConnectionError("caído"))
    servicio = _servicio(adaptador, umbral_fallos=1)
    servicio.firmar(SOLICITUD)
    llamadas = adaptador.llamadas

    resultado = servicio.firmar(SOLICITUD)

    assert resultado.estado == "pendiente"
    assert resultado.motivo == MOTIVO_CIRCUITO_ABIERTO
    assert adaptador.llamadas == llamadas  # fail-fast: el usuario no paga el timeout (EC009)


def test_los_logs_no_llevan_el_id_de_firma_ni_el_firmante(caplog) -> None:
    caplog.set_level("WARNING")

    _servicio(AdaptadorFalso(error=ConnectionError("caído"))).firmar(SOLICITUD)

    assert "propuesta-1" not in caplog.text
    assert "usuario-1" not in caplog.text
