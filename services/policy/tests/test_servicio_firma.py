"""Firmar la propuesta y guardar la evidencia de no repudio (HU-W03, CA-W03-03)."""

import threading

import pytest
from fakes import AclFalso, PlanificadorFalso, RatingFalso, RepositorioPropuestasFalso

from app.application.cliente_acl import ResultadoFirma
from app.application.documento_firma import hash_documento
from app.application.servicio_firma import (
    COBRO_NO_AUTORIZADO,
    PROPUESTA_NO_FIRMABLE,
    SERVICIO_FIRMA_NO_DISPONIBLE,
    VERSION_DOCUMENTO_DESACTUALIZADA,
    FirmaNoPermitida,
    PropuestaNoEncontrada,
    ServicioFirma,
)
from app.application.servicio_propuestas import ServicioPropuestas


def _armar(acl: AclFalso | None = None):
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    acl = acl or AclFalso()
    return ServicioFirma(repositorio, acl), repositorio, acl, propuesta["id"]


def test_firma_guarda_la_evidencia_y_pasa_la_propuesta_a_en_emision() -> None:
    servicio, repositorio, acl, propuesta_id = _armar()

    evidencia, estado = servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert estado == "EN_EMISION"
    assert evidencia["referencia_proveedor"] == f"fe-{propuesta_id}"
    assert evidencia["firmante_id"] == "usuario-1"
    assert repositorio.obtener(propuesta_id)["estado"] == "EN_EMISION"
    assert len(acl.solicitudes) == 1


def test_el_proveedor_solo_recibe_el_hash_del_documento_que_se_guarda() -> None:
    servicio, repositorio, acl, propuesta_id = _armar()

    servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    enviada = acl.solicitudes[0]
    guardado = repositorio.documentos_guardados[propuesta_id]
    assert enviada.documento_hash == hash_documento(guardado)  # se puede probar qué se firmó
    assert "185000" in guardado  # el contenido vive aquí, cifrado en la base real...
    assert "185000" not in repr(enviada)  # ...y nunca viaja al proveedor
    assert enviada.id_firma == propuesta_id


def test_firmar_dos_veces_devuelve_la_misma_evidencia_sin_llamar_al_proveedor() -> None:
    servicio, _, acl, propuesta_id = _armar()

    primera, _ = servicio.firmar(propuesta_id, True, "v1", "usuario-1")
    segunda, estado = servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert primera["id"] == segunda["id"]
    assert estado == "EN_EMISION"
    assert len(acl.solicitudes) == 1


def test_firmar_en_paralelo_deja_una_sola_evidencia() -> None:
    servicio, _, _, propuesta_id = _armar()
    ids: list[str] = []

    def firmar() -> None:
        ids.append(servicio.firmar(propuesta_id, True, "v1", "usuario-1")[0]["id"])

    hilos = [threading.Thread(target=firmar) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(set(ids)) == 1


def test_sin_autorizar_el_cobro_no_se_firma_ni_se_llama_al_proveedor() -> None:
    servicio, _, acl, propuesta_id = _armar()

    with pytest.raises(FirmaNoPermitida) as error:
        servicio.firmar(propuesta_id, False, "v1", "usuario-1")

    assert error.value.motivo == COBRO_NO_AUTORIZADO
    assert acl.solicitudes == []


def test_con_una_version_desactualizada_no_se_firma() -> None:
    servicio, _, acl, propuesta_id = _armar()

    with pytest.raises(FirmaNoPermitida) as error:
        servicio.firmar(propuesta_id, True, "v0", "usuario-1")

    assert error.value.motivo == VERSION_DOCUMENTO_DESACTUALIZADA
    assert acl.solicitudes == []


def test_una_propuesta_que_no_esta_aceptada_no_se_firma() -> None:
    servicio, repositorio, _, propuesta_id = _armar()
    repositorio._por_oferta["oferta-1"]["estado"] = "NO_EMITIDA"

    with pytest.raises(FirmaNoPermitida) as error:
        servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert error.value.motivo == PROPUESTA_NO_FIRMABLE


def test_una_propuesta_desconocida_no_se_firma() -> None:
    servicio, *_ = _armar()

    with pytest.raises(PropuestaNoEncontrada):
        servicio.firmar("no-existe", True, "v1", "usuario-1")


def test_si_el_proveedor_queda_pendiente_no_hay_evidencia_y_se_puede_reintentar() -> None:
    pendiente = ResultadoFirma(estado="pendiente", motivo="firma_no_disponible")
    servicio, repositorio, _, propuesta_id = _armar(AclFalso([pendiente]))

    with pytest.raises(FirmaNoPermitida) as error:
        servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert error.value.motivo == SERVICIO_FIRMA_NO_DISPONIBLE
    assert repositorio.obtener_evidencia(propuesta_id) is None
    assert repositorio.obtener(propuesta_id)["estado"] == "ACEPTADA"

    evidencia, estado = servicio.firmar(propuesta_id, True, "v1", "usuario-1")  # reintento
    assert estado == "EN_EMISION"
    assert evidencia["referencia_proveedor"]


def test_el_documento_a_firmar_sale_de_la_propuesta_guardada() -> None:
    servicio, _, _, propuesta_id = _armar()

    documento = servicio.documento(propuesta_id)

    assert documento["monto_mensual"] == {"valor": "185000", "moneda": "COP"}
    assert documento["meses"] == 60
    assert documento["version"] == "v1"


def _armar_con_planificador(planificador: PlanificadorFalso, acl: AclFalso | None = None):
    repositorio = RepositorioPropuestasFalso()
    propuesta = ServicioPropuestas(repositorio, RatingFalso()).aceptar("oferta-1", "usuario-1")
    servicio = ServicioFirma(repositorio, acl or AclFalso(), planificador)
    return servicio, repositorio, propuesta["id"]


def test_al_firmar_se_encola_la_emision() -> None:
    planificador = PlanificadorFalso()
    servicio, _, propuesta_id = _armar_con_planificador(planificador)

    servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert planificador.encolados == [(propuesta_id, 0)]


def test_una_firma_rechazada_no_encola_la_emision() -> None:
    planificador = PlanificadorFalso()
    pendiente = ResultadoFirma(estado="pendiente", motivo="firma_no_disponible")
    servicio, _, propuesta_id = _armar_con_planificador(planificador, AclFalso([pendiente]))

    with pytest.raises(FirmaNoPermitida):
        servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert planificador.encolados == []


def test_si_no_se_puede_encolar_la_firma_igual_queda_registrada() -> None:
    planificador = PlanificadorFalso(falla=True)
    servicio, repositorio, propuesta_id = _armar_con_planificador(planificador)

    evidencia, estado = servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert estado == "EN_EMISION"
    assert repositorio.obtener_evidencia(propuesta_id) is not None
    assert evidencia["referencia_proveedor"]


def test_volver_a_firmar_una_propuesta_que_quedo_sin_avanzar_la_vuelve_a_encolar() -> None:
    planificador = PlanificadorFalso()
    servicio, _, propuesta_id = _armar_con_planificador(planificador)
    servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert len(planificador.encolados) == 2


def test_volver_a_firmar_una_propuesta_ya_emitida_no_encola_nada() -> None:
    planificador = PlanificadorFalso()
    servicio, repositorio, propuesta_id = _armar_con_planificador(planificador)
    servicio.firmar(propuesta_id, True, "v1", "usuario-1")
    repositorio._propuesta(propuesta_id)["estado"] = "EMITIDA"

    servicio.firmar(propuesta_id, True, "v1", "usuario-1")

    assert len(planificador.encolados) == 1
