"""HU-M05 (KAN-274): el cliente reporta un siniestro desde la app y llega PENDIENTE a la mesa."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import AHORA, RepositorioFalso
from fastapi.testclient import TestClient

from app.application.clientes import ServicioNoDisponible, SolicitudRechazada
from app.application.servicio_siniestros import (
    MENSAJE_ALMACENAMIENTO,
    MENSAJE_REPORTE_DUPLICADO,
    MENSAJE_REPORTE_FOTO_INVALIDA,
    MENSAJE_REPORTE_FOTOS,
    MENSAJE_REPORTE_FUTURO,
    MENSAJE_REPORTE_POLIZA,
    MENSAJE_REPORTE_RELATO,
    MENSAJE_REPORTE_VIGENCIA,
    DependenciaNoDisponible,
    ErrorNegocio,
    ServicioSiniestros,
)
from app.main import create_app

ID = "0c9f2b7e-4a51-4c1e-9d6a-1f2e3d4c5b6a"
CLIENTE = "usr-cliente-1"


class PolicyDeCliente:
    def __init__(self, firmante=CLIENTE, falla=None):
        self.firmante = firmante
        self.falla = falla

    def obtener_poliza(self, poliza_id):
        if self.falla:
            raise self.falla
        return {
            "id": poliza_id,
            "estado": "EMITIDA",
            "firmante_id": self.firmante,
            "actor_usuario_id": "asesor-1",
            "vigencia_desde": "2026-01-01T00:00:00+00:00",
            "vigencia_hasta": "2027-01-01T00:00:00+00:00",
            "cobertura": {"valor": "52000000", "moneda": "COP"},
        }


class AlmacenFotos:
    """Archivos ya subidos por la app a nombre del siniestro."""

    def __init__(self, propietario=ID, estado="DISPONIBLE", falla=None):
        self.propietario = propietario
        self.estado = estado
        self.falla = falla

    def obtener(self, archivo_id):
        if self.falla:
            raise self.falla
        if archivo_id.startswith("inexistente"):
            return None
        return {
            "id": archivo_id,
            "tipo": "siniestro-foto",
            "estado": self.estado,
            "propietario_tipo": "siniestro",
            "propietario_id": self.propietario,
        }


def reporte(**cambios) -> dict:
    base = {
        "id_reporte": ID,
        "cliente_id": CLIENTE,
        "asegurado": {"nombre": "María Pérez", "documento": "1020304050"},
        "poliza_id": "pol-77",
        "cobertura_aplicada": "Vida grupo deudor con amparo de daños al bien financiado",
        "ocurrido_en": AHORA - timedelta(hours=2),
        "descripcion": "Me chocaron por detrás en un semáforo; daño en el bómper trasero.",
        "ubicacion": {
            "descripcion": "Cra 15 con Calle 93",
            "ciudad": "Bogotá",
            "latitud": 4.6767,
            "longitud": -74.0483,
        },
        "evidencias": [
            {
                "archivo_id": "arch-a",
                "tomada_en": AHORA - timedelta(hours=1),
                "latitud": 4.6767,
                "longitud": -74.0483,
            },
            {"archivo_id": "arch-b", "tomada_en": AHORA - timedelta(hours=1)},
        ],
    }
    return {**base, **cambios}


def _servicio(policy=None, almacen=None, repo=None):
    repo = repo or RepositorioFalso()
    bus = BusEnMemoria()
    servicio = ServicioSiniestros(
        repo,
        lambda: AHORA,
        policy=policy or PolicyDeCliente(),
        bus=bus,
        almacenamiento=almacen or AlmacenFotos(),
    )
    return servicio, repo, bus


class BusEnMemoria:
    def __init__(self):
        self.eventos = []

    def publicar(self, evento):
        self.eventos.append(evento)
        return True


def test_crea_el_caso_pendiente_con_radicado_plazo_y_fotos_geoetiquetadas():
    servicio, repo, bus = _servicio()
    s = servicio.reportar(reporte())
    assert s["estado"] == "PENDIENTE"
    assert s["radicado"] == "SIN-2026-1001"
    assert s["reportado_por"] == CLIENTE
    assert s["notificado_en"] == AHORA
    # W04-7: dos días hábiles a las 5:00 p. m. de Colombia (AHORA es miércoles 28 oct).
    assert s["vence_dictamen_en"].isoformat() == "2026-10-30T17:00:00-05:00"
    assert s["suma_reclamada"] == {"valor": "52000000", "moneda": "COP"}
    fotos = s["evidencias"]
    assert [f["tipo"] for f in fotos] == ["FOTO", "FOTO"]
    assert fotos[0]["geoetiquetada"] is True and fotos[0]["latitud"] == 4.6767
    assert fotos[1]["geoetiquetada"] is False and fotos[1]["latitud"] is None
    # Aparece en la bandeja del operador (W04-01).
    assert [c["id"] for c in servicio.listar("PENDIENTE")] == [ID]
    assert bus.eventos[0]["tipo"] == "siniestro.reportado"
    assert bus.eventos[0]["datos"]["fotos"] == 2


def test_el_evento_cumple_su_esquema_y_no_lleva_datos_personales():
    from app.application.bus_eventos import BusEventosLog

    servicio, _, bus = _servicio()
    servicio.reportar(reporte())
    evento = bus.eventos[0]
    assert BusEventosLog().publicar(evento) is True
    texto = str(evento)
    assert "María" not in texto and "1020304050" not in texto and "semáforo" not in texto


def test_reportar_dos_veces_con_el_mismo_id_devuelve_el_mismo_caso():
    servicio, repo, bus = _servicio()
    primero = servicio.reportar(reporte())
    segundo = servicio.reportar(reporte())
    assert segundo["id"] == primero["id"] and segundo["radicado"] == primero["radicado"]
    assert len(repo.siniestros) == 1 and len(bus.eventos) == 1


def test_el_mismo_id_de_otro_cliente_no_revela_el_caso():
    servicio, _, _ = _servicio()
    servicio.reportar(reporte())
    with pytest.raises(ErrorNegocio, match=MENSAJE_REPORTE_DUPLICADO):
        servicio.reportar(reporte(cliente_id="otro-cliente"))


@pytest.mark.parametrize(
    ("cambios", "mensaje"),
    [
        ({"ocurrido_en": AHORA + timedelta(minutes=5)}, MENSAJE_REPORTE_FUTURO),
        ({"ocurrido_en": datetime(2025, 6, 1, tzinfo=UTC)}, MENSAJE_REPORTE_VIGENCIA),
        ({"descripcion": "  corto "}, MENSAJE_REPORTE_RELATO),
        ({"descripcion": "x" * 1001}, MENSAJE_REPORTE_RELATO),
        ({"evidencias": []}, MENSAJE_REPORTE_FOTOS),
    ],
)
def test_valida_fecha_relato_y_cantidad_de_fotos(cambios, mensaje):
    servicio, repo, _ = _servicio()
    with pytest.raises(ErrorNegocio, match=mensaje):
        servicio.reportar(reporte(**cambios))
    assert repo.siniestros == {}


def test_cuatro_fotos_son_demasiadas():
    servicio, _, _ = _servicio()
    fotos = [{"archivo_id": f"arch-{i}", "tomada_en": AHORA} for i in range(4)]
    with pytest.raises(ErrorNegocio, match=MENSAJE_REPORTE_FOTOS):
        servicio.reportar(reporte(evidencias=fotos))


@pytest.mark.parametrize(
    "policy",
    [PolicyDeCliente(firmante="otra-persona"), PolicyDeCliente(falla=SolicitudRechazada("x"))],
)
def test_solo_el_titular_reporta_sobre_su_poliza(policy):
    servicio, _, _ = _servicio(policy=policy)
    with pytest.raises(ErrorNegocio, match=MENSAJE_REPORTE_POLIZA):
        servicio.reportar(reporte())


@pytest.mark.parametrize(
    ("almacen", "fotos"),
    [
        (AlmacenFotos(propietario="otro-siniestro"), None),  # foto de otro caso
        (AlmacenFotos(estado="PENDIENTE"), None),  # subida sin confirmar
        (AlmacenFotos(), [{"archivo_id": "inexistente-1", "tomada_en": AHORA}]),
        (AlmacenFotos(), [{"archivo_id": "a", "tomada_en": AHORA}] * 2),  # repetida
    ],
)
def test_rechaza_fotos_ajenas_sin_confirmar_inexistentes_o_repetidas(almacen, fotos):
    servicio, repo, _ = _servicio(almacen=almacen)
    datos = reporte(evidencias=fotos) if fotos else reporte()
    with pytest.raises(ErrorNegocio, match=MENSAJE_REPORTE_FOTO_INVALIDA):
        servicio.reportar(datos)
    assert repo.siniestros == {}


def test_almacenamiento_caido_es_reintentable():
    servicio, repo, _ = _servicio(almacen=AlmacenFotos(falla=ServicioNoDisponible("x")))
    with pytest.raises(DependenciaNoDisponible, match=MENSAJE_ALMACENAMIENTO):
        servicio.reportar(reporte())
    assert repo.siniestros == {}


def test_los_casos_del_cliente_solo_son_los_suyos():
    servicio, repo, _ = _servicio()
    servicio.reportar(reporte())
    assert [c["id"] for c in servicio.de_cliente(CLIENTE)] == [ID]
    assert servicio.de_cliente("otro") == []


# ---------- API ----------


def _api(servicio):
    return TestClient(create_app(servicio=servicio))


def _json(datos: dict) -> dict:
    datos = {**datos, "ocurrido_en": datos["ocurrido_en"].isoformat()}
    datos["evidencias"] = [
        {**e, "tomada_en": e["tomada_en"].isoformat()} for e in datos["evidencias"]
    ]
    return datos


def test_post_siniestros_crea_y_get_de_cliente_lo_lista():
    servicio, _, _ = _servicio()
    with _api(servicio) as api:
        r = api.post("/siniestros", json=_json(reporte()))
        assert r.status_code == 200, r.text
        assert r.json()["estado"] == "PENDIENTE"
        lista = api.get(f"/clientes/{CLIENTE}/siniestros").json()
        assert [c["id"] for c in lista] == [ID]


def test_post_siniestros_regla_incumplida_es_409_con_mensaje_para_el_cliente():
    servicio, _, _ = _servicio()
    with _api(servicio) as api:
        r = api.post("/siniestros", json=_json(reporte(descripcion="corto")))
        assert r.status_code == 409
        assert r.json()["detail"] == MENSAJE_REPORTE_RELATO


def test_post_siniestros_valida_la_forma():
    servicio, _, _ = _servicio()
    with _api(servicio) as api:
        r = api.post("/siniestros", json={**_json(reporte()), "id_reporte": "corto"})
        assert r.status_code == 422
