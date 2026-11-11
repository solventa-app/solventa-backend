"""KAN-259 (HU-M04, Fase 4, D8): el endpoint interno de coberturas paramétricas.

Solo lo llama CLAIMS. Prueba con `RepositorioCoberturasParametricasFalso` (sin PostgreSQL real,
mismo patrón que el resto de POLICY)."""

from contextlib import contextmanager
from datetime import UTC, datetime

from fakes import RepositorioCoberturasParametricasFalso
from fastapi.testclient import TestClient

from app.application.repositorio_coberturas_parametricas import _fila
from app.main import create_app

VIGENCIA_DESDE = datetime(2026, 1, 1, tzinfo=UTC)
VIGENCIA_HASTA = datetime(2027, 1, 1, tzinfo=UTC)


@contextmanager
def _cliente(coberturas: RepositorioCoberturasParametricasFalso):
    app = create_app(
        servicio_propuestas=object(),
        servicio_firma=object(),
        servicio_emision=object(),
        servicio_polizas=object(),
        servicio_firma_y_pago=object(),
        servicio_purga=object(),
        repositorio_coberturas=coberturas,
    )
    with TestClient(app) as cliente:
        yield cliente


def test_vuelo_vigente_que_calza_exacto():
    coberturas = RepositorioCoberturasParametricasFalso()
    coberturas.crear_vuelo(
        "poliza-1",
        "500000",
        "COP",
        VIGENCIA_DESDE,
        VIGENCIA_HASTA,
        numero_vuelo="AV9340",
        fecha_salida=datetime(2026, 11, 11).date(),
        aeropuerto_origen="BOG",
        umbral_retraso_min=120,
    )
    with _cliente(coberturas) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas",
            params={"tipo": "VUELO", "vuelo": "AV9340", "fecha": "2026-11-11"},
        )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert len(cuerpo) == 1
    assert cuerpo[0]["poliza_id"] == "poliza-1"
    assert cuerpo[0]["umbral_retraso_min"] == 120


def test_vuelo_sin_coincidencia_devuelve_vacio():
    coberturas = RepositorioCoberturasParametricasFalso()
    with _cliente(coberturas) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas",
            params={"tipo": "VUELO", "vuelo": "XX0000", "fecha": "2026-11-11"},
        )
    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_vuelo_sin_parametros_responde_422():
    with _cliente(RepositorioCoberturasParametricasFalso()) as cliente:
        respuesta = cliente.get("/interno/coberturas-parametricas", params={"tipo": "VUELO"})
    assert respuesta.status_code == 422


def test_clima_vigente_por_celda():
    coberturas = RepositorioCoberturasParametricasFalso()
    coberturas.crear_clima(
        "poliza-2",
        "400000",
        "COP",
        VIGENCIA_DESDE,
        VIGENCIA_HASTA,
        latitud=4.62,
        longitud=-74.09,
        variable="PRECIPITACION_MM",
        umbral="50",
        ventana_horas=24,
    )
    with _cliente(coberturas) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas",
            params={"tipo": "CLIMA", "celda": "4.6198,-74.0897", "variable": "PRECIPITACION_MM"},
        )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert len(cuerpo) == 1
    assert cuerpo[0]["umbral"] == "50"


def test_clima_con_lat_lon_sueltos():
    coberturas = RepositorioCoberturasParametricasFalso()
    coberturas.crear_clima(
        "poliza-3",
        "400000",
        "COP",
        VIGENCIA_DESDE,
        VIGENCIA_HASTA,
        latitud=4.62,
        longitud=-74.09,
        variable="VIENTO_KMH",
        umbral="80",
        ventana_horas=24,
    )
    with _cliente(coberturas) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas",
            params={"tipo": "CLIMA", "lat": 4.619, "lon": -74.091},
        )
    assert respuesta.status_code == 200
    assert len(respuesta.json()) == 1


def test_clima_sin_parametros_responde_422():
    with _cliente(RepositorioCoberturasParametricasFalso()) as cliente:
        respuesta = cliente.get("/interno/coberturas-parametricas", params={"tipo": "CLIMA"})
    assert respuesta.status_code == 422


def test_poliza_id_devuelve_su_cobertura_vigente():
    """Fase 5 (KAN-259): el BFF consulta por `poliza_id` para `ResumenCobertura`, sin `tipo`."""
    coberturas = RepositorioCoberturasParametricasFalso()
    coberturas.crear_vuelo(
        "poliza-9",
        "500000",
        "COP",
        VIGENCIA_DESDE,
        VIGENCIA_HASTA,
        numero_vuelo="AV9340",
        fecha_salida=datetime(2026, 11, 11).date(),
        aeropuerto_origen="BOG",
        umbral_retraso_min=120,
    )
    with _cliente(coberturas) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas", params={"poliza_id": "poliza-9"}
        )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert len(cuerpo) == 1
    assert cuerpo[0]["poliza_id"] == "poliza-9"


def test_poliza_id_sin_coberturas_devuelve_vacio():
    with _cliente(RepositorioCoberturasParametricasFalso()) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas", params={"poliza_id": "poliza-sin-cobertura"}
        )
    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_tipo_invalido_responde_422():
    with _cliente(RepositorioCoberturasParametricasFalso()) as cliente:
        respuesta = cliente.get(
            "/interno/coberturas-parametricas", params={"tipo": "OTRO", "vuelo": "AV1"}
        )
    assert respuesta.status_code == 422


def test_fila_formatea_el_monto_y_los_decimales():
    fila = _fila(
        {
            "id": "c1",
            "poliza_id": "p1",
            "tipo": "CLIMA",
            "monto_fijo": 400000,
            "moneda": "COP",
            "vigencia_desde": VIGENCIA_DESDE,
            "vigencia_hasta": VIGENCIA_HASTA,
            "estado": "VIGENTE",
            "numero_vuelo": None,
            "fecha_salida": None,
            "aeropuerto_origen": None,
            "umbral_retraso_min": None,
            "latitud": 4.62,
            "longitud": -74.09,
            "variable": "PRECIPITACION_MM",
            "umbral": 50,
            "ventana_horas": 24,
            "creado_en": VIGENCIA_DESDE,
        }
    )
    assert fila["monto_fijo"] == {"valor": "400000", "moneda": "COP"}
    assert fila["latitud"] == 4.62
    assert fila["umbral"] == "50"
