"""KAN-290: la cotización de la póliza de auto en el BFF (servicio, GraphQL y API de socios)."""

import asyncio

from fakes import OFERTA_AUTO, AclFalso, RatingFalso, RiskFalso, clientes_falsos

from app.clientes import DatosInsuficientes, ErrorServicio, VehiculoRechazado
from app.identificadores import normalizar_placa, vehiculo_id_desde_placa
from app.servicio_oferta import (
    FUENTES_VEHICULO,
    MENSAJE_CIUDAD_INVALIDA,
    MENSAJE_KILOMETRAJE_INVALIDO,
    MENSAJE_PLACA_INVALIDA,
    ErrorOfertaDatos,
    ServicioOferta,
)

DOCUMENTO = "CC-1020304050"


def _cotizar(servicio, placa="abc-123", km=12000, ciudad="Bogotá"):
    return asyncio.run(
        servicio.solicitar_auto(
            "c-1", DOCUMENTO, placa, km, ciudad, "PARTICULAR", "DIEZ_POR_CIENTO"
        )
    )


def test_normaliza_la_placa_y_rechaza_las_que_no_lo_son() -> None:
    assert normalizar_placa(" abc-123 ") == "ABC123"
    assert normalizar_placa("vwx 23d") == "VWX23D"
    assert normalizar_placa("12ABC") is None
    assert vehiculo_id_desde_placa("ABC123", "k") != vehiculo_id_desde_placa("ABC124", "k")


def test_flujo_sano_la_placa_solo_llega_al_acl() -> None:
    acl, risk, rating = AclFalso(), RiskFalso(), RatingFalso()
    servicio = ServicioOferta(clientes_falsos(acl=acl, risk=risk, rating=rating), "clave")

    assert _cotizar(servicio) == OFERTA_AUTO

    vehiculo_id = vehiculo_id_desde_placa("ABC123", "clave")
    assert sorted(f for _, _, f in acl.vehiculos) == sorted(FUENTES_VEHICULO)
    assert {(v, p) for v, p, _ in acl.vehiculos} == {(vehiculo_id, "ABC123")}
    vid, titular, fuentes = risk.vehiculos[0]
    assert vid == vehiculo_id and len(fuentes) == 3 and titular != DOCUMENTO
    solicitud = rating.solicitudes_auto[0]
    assert solicitud["vehiculo_id"] == vehiculo_id
    assert solicitud["placa_enmascarada"] == "ABC•••"
    assert solicitud["operation_time"] == "1700000000.2"
    assert "ABC123" not in str(risk.vehiculos) + str(rating.solicitudes_auto)


def test_datos_invalidos_no_consultan_nada() -> None:
    acl = AclFalso()
    servicio = ServicioOferta(clientes_falsos(acl=acl), "clave")
    for kwargs, mensaje in (
        ({"placa": "12ABC"}, MENSAJE_PLACA_INVALIDA),
        ({"km": 250000}, MENSAJE_KILOMETRAJE_INVALIDO),
        ({"ciudad": " "}, MENSAJE_CIUDAD_INVALIDA),
    ):
        resultado = _cotizar(servicio, **kwargs)
        assert isinstance(resultado, ErrorOfertaDatos) and resultado.mensaje == mensaje
    assert getattr(acl, "vehiculos", []) == []


def test_una_fuente_caida_degrada_sin_error() -> None:
    risk = RiskFalso()
    servicio = ServicioOferta(
        clientes_falsos(acl=AclFalso(fallan={"fasecolda-valor"}), risk=risk), "clave"
    )
    assert _cotizar(servicio) == OFERTA_AUTO
    fuentes = {f["fuente"]: f for f in risk.vehiculos[0][2]}
    assert fuentes["fasecolda-valor"]["degradado"] is True


def test_errores_de_rating_se_traducen_sin_5xx() -> None:
    casos = (
        (VehiculoRechazado("VEHICULO_NO_ASEGURABLE", "Hurtado."), "VEHICULO_NO_ASEGURABLE", False),
        (DatosInsuficientes(), "DATOS_INSUFICIENTES", True),
        (ErrorServicio("HTTP 503"), "FUENTES_NO_DISPONIBLES", True),
    )
    for error, motivo, reintentar in casos:
        servicio = ServicioOferta(clientes_falsos(rating=RatingFalso(error=error)), "clave")
        resultado = _cotizar(servicio)
        assert isinstance(resultado, ErrorOfertaDatos)
        assert (resultado.motivo, resultado.puede_reintentar) == (motivo, reintentar)


def test_mutacion_solicitar_oferta_auto_por_graphql() -> None:
    from fakes import AuthFalso, token_de_prueba
    from fastapi.testclient import TestClient

    from app.configuracion import Configuracion
    from app.main import create_app

    consulta = """mutation($e: SolicitudOfertaAutoInput!) {
      solicitarOfertaAuto(entrada: $e) {
        ... on Oferta { producto tipo vehiculo { placaEnmascarada marca modelo }
                        coberturas { tipo } factoresTarifa { nombre factor } }
        ... on ErrorOferta { mensaje motivo }
      }
    }"""
    auth = AuthFalso()
    cliente = TestClient(
        create_app(config=Configuracion.desde_entorno(), clientes=clientes_falsos(auth=auth))
    )
    with cliente as c:
        consentimiento = c.post(
            "/graphql/web",
            json={
                "query": "mutation($e: ConsentimientoInput!) "
                "{ registrarConsentimiento(entrada: $e) { id } }",
                "variables": {
                    "e": {
                        "finalidad": "f",
                        "versionTexto": "v1",
                        "documentoIdentidad": "1020304050",
                    }
                },
            },
            headers={"Authorization": f"Bearer {token_de_prueba('ASESOR', 'u-1')}"},
        ).json()["data"]["registrarConsentimiento"]["id"]
        r = c.post(
            "/graphql/web",
            json={
                "query": consulta,
                "variables": {
                    "e": {
                        "consentimientoId": consentimiento,
                        "documentoIdentidad": "1020304050",
                        "placa": "ABC123",
                        "kilometrajeAnual": 12000,
                        "ciudadCirculacion": "Bogotá",
                    }
                },
            },
            headers={"Authorization": f"Bearer {token_de_prueba('ASESOR', 'u-1')}"},
        ).json()
    oferta = r["data"]["solicitarOfertaAuto"]
    assert oferta["producto"] == "AUTO" and oferta["tipo"] == "COMPLETA"
    assert oferta["vehiculo"] == {"placaEnmascarada": "ABC•••", "marca": "MAZDA", "modelo": 2024}
    assert [c["tipo"] for c in oferta["coberturas"]] == ["RESPONSABILIDAD_CIVIL", "ASISTENCIA"]
