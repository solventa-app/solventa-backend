"""Orquestación de `solicitarOferta` con dobles (sin red)."""

import asyncio
import logging

import pytest
from fakes import (
    CONSENTIMIENTO_VIGENTE,
    OFERTA_COMPLETA,
    AclFalso,
    AuthFalso,
    RatingFalso,
    RiskFalso,
    clientes_falsos,
)

from app.clientes import DatosInsuficientes, ErrorServicio, SolicitudInvalida
from app.servicio_oferta import (
    FUENTES,
    MENSAJE_DATOS_INSUFICIENTES,
    MENSAJE_DIRECCION_INVALIDA,
    MENSAJE_DOCUMENTO_INVALIDO,
    MENSAJE_PLAZO_INVALIDO,
    MENSAJE_VALOR_INVALIDO,
    ErrorOfertaDatos,
    ServicioOferta,
)

DOCUMENTO = "CC-1020304050"
DIRECCION = "Calle 100 # 15-20, Bogotá"


def _solicitar(
    servicio: ServicioOferta,
    consentimiento_id: str = "c-1",
    documento: str = DOCUMENTO,
    valor: str = "200000000",
    plazo: int = 240,
    direccion: str = DIRECCION,
):
    return asyncio.run(servicio.solicitar(consentimiento_id, documento, valor, plazo, direccion))


def test_flujo_sano_consulta_las_5_fuentes_y_devuelve_la_oferta_de_rating() -> None:
    acl, risk, rating = AclFalso(), RiskFalso(), RatingFalso()
    servicio = ServicioOferta(clientes_falsos(acl=acl, risk=risk, rating=rating), "clave")

    resultado = _solicitar(servicio)

    assert resultado == OFERTA_COMPLETA
    assert sorted(llamada[2] for llamada in acl.llamadas) == sorted(FUENTES)
    cliente_id, fuentes = risk.llamadas[0]
    assert len(fuentes) == 5
    # RATING recibe el `operation_time` de RISK, el valor del crédito como cobertura,
    # el plazo y la dirección (para la prima).
    assert rating.llamadas == [(cliente_id, "1700000000.1", "200000000", 240, DIRECCION)]


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

    assert resultado == ErrorOfertaDatos(
        resultado.mensaje,
        puede_reintentar=False,
        motivo="CONSENTIMIENTO_NO_VIGENTE",
    )
    assert acl.llamadas == []


def test_consentimiento_revocado_no_consulta_ninguna_fuente() -> None:
    revocado = {**CONSENTIMIENTO_VIGENTE, "vigente": False, "estado": "REVOCADO"}
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(auth=AuthFalso({"c-1": revocado}), acl=acl), "c")

    resultado = _solicitar(servicio)

    assert isinstance(resultado, ErrorOfertaDatos)
    assert resultado.puede_reintentar is False
    assert resultado.motivo == "CONSENTIMIENTO_NO_VIGENTE"
    assert acl.llamadas == []


def test_consentimiento_de_otro_titular_no_consulta_ninguna_fuente() -> None:
    de_otro = {**CONSENTIMIENTO_VIGENTE, "titular_id": "hash-de-otro-documento"}
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(auth=AuthFalso({"c-1": de_otro}), acl=acl), "clave")

    resultado = _solicitar(servicio)

    assert isinstance(resultado, ErrorOfertaDatos)
    assert resultado.puede_reintentar is False
    assert resultado.motivo == "CONSENTIMIENTO_NO_VIGENTE"
    assert acl.llamadas == []


def test_auth_caido_da_error_con_reintento_y_no_consulta_fuentes() -> None:
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(auth=AuthFalso(falla=True), acl=acl), "clave")

    resultado = _solicitar(servicio)

    assert resultado.puede_reintentar is True
    assert resultado.motivo == "FUENTES_NO_DISPONIBLES"
    assert acl.llamadas == []


def test_risk_caido_da_error_con_reintento() -> None:
    rating = RatingFalso()
    servicio = ServicioOferta(clientes_falsos(risk=RiskFalso(falla=True), rating=rating), "c")

    resultado = _solicitar(servicio)

    assert isinstance(resultado, ErrorOfertaDatos)
    assert resultado.puede_reintentar is True
    assert resultado.motivo == "FUENTES_NO_DISPONIBLES"
    assert rating.llamadas == []


def test_rating_caido_da_error_con_reintento() -> None:
    servicio = ServicioOferta(clientes_falsos(rating=RatingFalso(error=ErrorServicio("x"))), "c")

    resultado = _solicitar(servicio)

    assert resultado.puede_reintentar is True
    assert resultado.motivo == "FUENTES_NO_DISPONIBLES"


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


# ---------- CA-W01-05: datos insuficientes ----------


def test_sin_historial_crediticio_da_error_con_reintento() -> None:
    servicio = ServicioOferta(clientes_falsos(rating=RatingFalso(error=DatosInsuficientes())), "c")

    resultado = _solicitar(servicio)

    assert resultado == ErrorOfertaDatos(
        MENSAJE_DATOS_INSUFICIENTES,
        puede_reintentar=True,
        motivo="DATOS_INSUFICIENTES",
    )


# ---------- B3: validación de la solicitud ----------


@pytest.mark.parametrize(
    ("cambios", "mensaje"),
    [
        ({"documento": ""}, MENSAJE_DOCUMENTO_INVALIDO),
        ({"documento": "abc12"}, MENSAJE_DOCUMENTO_INVALIDO),
        ({"documento": "1" * 21}, MENSAJE_DOCUMENTO_INVALIDO),
        ({"valor": "no-es-un-numero"}, MENSAJE_VALOR_INVALIDO),
        ({"valor": "0"}, MENSAJE_VALOR_INVALIDO),
        ({"valor": "-5"}, MENSAJE_VALOR_INVALIDO),
        ({"valor": "Infinity"}, MENSAJE_VALOR_INVALIDO),
        ({"plazo": 0}, MENSAJE_PLAZO_INVALIDO),
        ({"plazo": 361}, MENSAJE_PLAZO_INVALIDO),
        ({"direccion": "   "}, MENSAJE_DIRECCION_INVALIDA),
        ({"direccion": "x" * 201}, MENSAJE_DIRECCION_INVALIDA),
    ],
)
def test_solicitud_invalida_da_error_sin_reintento_y_no_consulta_nada(cambios, mensaje) -> None:
    auth, acl = AuthFalso(), AclFalso()
    servicio = ServicioOferta(clientes_falsos(auth=auth, acl=acl), "clave")

    resultado = _solicitar(servicio, **cambios)

    assert resultado == ErrorOfertaDatos(mensaje, puede_reintentar=False)
    assert acl.llamadas == []


def test_los_limites_del_plazo_son_validos() -> None:
    for plazo in (1, 360):
        servicio = ServicioOferta(clientes_falsos(), "clave")
        assert not isinstance(_solicitar(servicio, plazo=plazo), ErrorOfertaDatos)


def test_los_logs_no_llevan_la_direccion(caplog) -> None:
    servicio = ServicioOferta(clientes_falsos(acl=AclFalso({"camara-comercio"})), "clave")

    with caplog.at_level(logging.DEBUG):
        _solicitar(servicio)

    assert DIRECCION not in caplog.text
