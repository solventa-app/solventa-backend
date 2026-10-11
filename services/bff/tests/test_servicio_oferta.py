"""Orquestación de `solicitarOferta` con dobles (sin red)."""

import asyncio
import logging

from fakes import (
    CONSENTIMIENTO_VIGENTE,
    OFERTA_COMPLETA,
    AclFalso,
    AuthFalso,
    RatingFalso,
    RiskFalso,
    clientes_falsos,
)

from app.clientes import ErrorServicio, SolicitudInvalida
from app.servicio_oferta import FUENTES, ErrorOfertaDatos, ServicioOferta

DOCUMENTO = "CC-1020304050"


def _solicitar(servicio: ServicioOferta, consentimiento_id: str = "c-1"):
    return asyncio.run(servicio.solicitar(consentimiento_id, DOCUMENTO, "200000000"))


def test_flujo_sano_consulta_las_5_fuentes_y_devuelve_la_oferta_de_rating() -> None:
    acl, risk, rating = AclFalso(), RiskFalso(), RatingFalso()
    servicio = ServicioOferta(clientes_falsos(acl=acl, risk=risk, rating=rating), "clave")

    resultado = _solicitar(servicio)

    assert resultado == OFERTA_COMPLETA
    assert sorted(llamada[2] for llamada in acl.llamadas) == sorted(FUENTES)
    cliente_id, fuentes = risk.llamadas[0]
    assert len(fuentes) == 5
    # RATING recibe el `operation_time` de RISK y el valor del crédito como cobertura.
    assert rating.llamadas == [(cliente_id, "1700000000.1", "200000000")]


def test_el_cliente_id_es_opaco_nunca_el_documento() -> None:
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(acl=acl), "clave")

    _solicitar(servicio)

    assert all(DOCUMENTO not in llamada[0] for llamada in acl.llamadas)


def test_si_el_acl_no_responde_esa_fuente_va_degradada_a_risk() -> None:
    risk = RiskFalso()
    servicio = ServicioOferta(clientes_falsos(acl=AclFalso({"afiliacion-pila"}), risk=risk), "c")

    resultado = _solicitar(servicio)

    assert not isinstance(resultado, ErrorOfertaDatos)
    fuentes = {f["fuente"]: f for f in risk.llamadas[0][1]}
    assert fuentes["afiliacion-pila"]["degradado"] is True
    assert fuentes["afiliacion-pila"]["datos"] == {}
    assert fuentes["cuentas-bancarias"]["degradado"] is False


def test_sin_consentimiento_no_consulta_ninguna_fuente() -> None:
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(acl=acl), "clave")

    resultado = _solicitar(servicio, consentimiento_id="no-existe")

    assert resultado == ErrorOfertaDatos(resultado.mensaje, puede_reintentar=False)
    assert acl.llamadas == []


def test_consentimiento_revocado_no_consulta_ninguna_fuente() -> None:
    revocado = {**CONSENTIMIENTO_VIGENTE, "vigente": False}
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(auth=AuthFalso({"c-1": revocado}), acl=acl), "c")

    resultado = _solicitar(servicio)

    assert isinstance(resultado, ErrorOfertaDatos)
    assert resultado.puede_reintentar is False
    assert acl.llamadas == []


def test_auth_caido_da_error_con_reintento_y_no_consulta_fuentes() -> None:
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(auth=AuthFalso(falla=True), acl=acl), "clave")

    resultado = _solicitar(servicio)

    assert resultado.puede_reintentar is True
    assert acl.llamadas == []


def test_risk_caido_da_error_con_reintento() -> None:
    rating = RatingFalso()
    servicio = ServicioOferta(clientes_falsos(risk=RiskFalso(falla=True), rating=rating), "c")

    resultado = _solicitar(servicio)

    assert isinstance(resultado, ErrorOfertaDatos)
    assert resultado.puede_reintentar is True
    assert rating.llamadas == []


def test_rating_caido_da_error_con_reintento() -> None:
    servicio = ServicioOferta(clientes_falsos(rating=RatingFalso(error=ErrorServicio("x"))), "c")

    resultado = _solicitar(servicio)

    assert resultado.puede_reintentar is True


def test_valor_invalido_da_error_sin_reintento() -> None:
    rating = RatingFalso(error=SolicitudInvalida("cobertura"))
    servicio = ServicioOferta(clientes_falsos(rating=rating), "clave")

    resultado = _solicitar(servicio)

    assert resultado.puede_reintentar is False


def test_los_logs_no_llevan_el_documento(caplog) -> None:
    servicio = ServicioOferta(clientes_falsos(acl=AclFalso({"camara-comercio"})), "clave")

    with caplog.at_level(logging.DEBUG):
        _solicitar(servicio)

    assert caplog.records, "se esperaba al menos un log del flujo"
    assert DOCUMENTO not in caplog.text
