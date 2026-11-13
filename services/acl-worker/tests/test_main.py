"""Pruebas de los endpoints del ACL Worker. Los proveedores apuntan a un
puerto sin nadie escuchando (conexión rechazada, falla rápida) para verificar
que ninguna ruta devuelve nunca un 5xx crudo (regla de arquitectura #4),
incluso sin levantar los stubs de verdad."""

from fastapi.testclient import TestClient

from app.application.configuracion import Configuracion
from app.main import create_app

CONFIG_SIN_PROVEEDORES = Configuracion(
    redis_url="redis://localhost:6399/0",  # puerto sin Redis real: la caché degrada a None
    open_finance_url="http://127.0.0.1:1",
    open_data_url="http://127.0.0.1:1",
    pasarela_url="http://127.0.0.1:1",
    # Sin esto usarían los puertos por defecto (8104 a 8106) y la prueba dependería de si el
    # stack local está encendido.
    firma_url="http://127.0.0.1:1",
    kyc_url="http://127.0.0.1:1",
    notificaciones_url="http://127.0.0.1:1",
    vehiculos_url="http://127.0.0.1:1",
    prestadores_url="http://127.0.0.1:1",
    clima_url="http://127.0.0.1:1",
    vuelos_url="http://127.0.0.1:1",
    timeout_s=0.2,
    breaker_umbral_fallos=3,
    breaker_ttl_segundos=5,
    sonda_intervalo_segundos=60,
    cache_ttl_segundos=300,
    # sin Redis real: el productor de reconciliación degrada a no-op
    cola_reconciliacion_redis_url="redis://localhost:6399/2",
)


def test_health() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["estado"] == "ok"


def test_metrics_expuestas() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        assert client.get("/metrics").status_code == 200


def test_circuitos_inicia_todo_cerrado() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.get("/circuitos")
        assert r.status_code == 200
        assert set(r.json().values()) == {"closed"}


def test_fuente_desconocida_responde_404_no_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/fuentes/consultar",
            json={"cliente_id": "c-1", "consentimiento_id": "k-1", "fuente": "no-existe"},
        )
        assert r.status_code == 404


def test_consultar_fuente_con_proveedor_caido_degrada_sin_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/fuentes/consultar",
            json={"cliente_id": "c-1", "consentimiento_id": "k-1", "fuente": "cuentas-bancarias"},
        )
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["degradado"] is True
        assert cuerpo["de_cache"] is False


def test_cobrar_con_pasarela_caida_queda_pendiente_sin_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/pagos/cobrar",
            json={
                "id_intento_cobro": "idem-1",
                "poliza_id": "pol-1",
                "monto": "150000.00",
                "moneda": "COP",
                "token_medio_pago": "tok_abc123",
            },
        )
        assert r.status_code == 200
        assert r.json()["estado"] == "pendiente"


def test_consultar_cobro_con_pasarela_caida_es_desconocido_sin_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.get("/pagos/cobros/idem-1")
        assert r.status_code == 200
        assert r.json() == {
            "resultado": "desconocido",
            "estado": None,
            "referencia": None,
            "motivo": None,
        }


def test_invalidar_cache_fuentes_responde_200_sin_cliente_en_url() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/fuentes/invalidar-cache", json={"cliente_id": "c-1"})
        assert r.status_code == 200
        assert r.json() == {"invalidado": True}


def test_presupuesto_por_proveedor_ec008_se_acota_por_timeout_s(monkeypatch) -> None:
    monkeypatch.setenv("TIMEOUT_MS", "700")
    monkeypatch.setenv("PRESUPUESTO_MS_OPEN_FINANCE", "120")
    monkeypatch.setenv("PRESUPUESTO_MS_OPEN_DATA", "120")
    monkeypatch.setenv("PRESUPUESTO_MS_PASARELA", "120")
    monkeypatch.setenv("CACHE_SERVIR_EN_TTL", "true")

    cfg = Configuracion.desde_entorno()
    assert cfg.presupuesto_proveedor_s("open-finance") == 0.12
    assert cfg.presupuesto_proveedor_s("open-data") == 0.12
    assert cfg.presupuesto_proveedor_s("pasarela") == 0.12
    assert cfg.cache_servir_en_ttl is True


def test_firmar_con_el_proveedor_caido_responde_200_pendiente_sin_5xx() -> None:
    # FIRMA_URL apunta a un puerto sin servicio: el ACL degrada, no propaga el fallo.
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/firma/firmar",
            json={
                "id_firma": "p-1",
                "documento_hash": "a" * 64,
                "version_documento": "v1",
                "firmante_id": "u-1",
            },
        )

    assert r.status_code == 200
    assert r.json()["estado"] == "pendiente"
    assert r.json()["motivo"] == "firma_no_disponible"


def test_verificar_kyc_con_el_proveedor_caido_responde_200_pendiente_sin_5xx() -> None:
    # KYC_URL apunta a un puerto sin servicio: el ACL degrada, no propaga el fallo.
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/kyc/verificar", json={"id_verificacion": "p-1", "cliente_id": "cliente-opaco"}
        )

    assert r.status_code == 200
    assert r.json()["estado"] == "pendiente"
    assert r.json()["motivo"] == "kyc_no_disponible"


def test_enviar_notificacion_con_el_proveedor_caido_responde_200_pendiente_sin_5xx() -> None:
    # NOTIFICACIONES_URL apunta a un puerto sin servicio: el ACL degrada, no propaga el fallo.
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/notificaciones/enviar",
            json={
                "id_notificacion": "n-1",
                "plantilla": "poliza-emitida",
                "destinatario_id": "u-1",
                "datos": {"numero_poliza": "SOL-2026-000001"},
            },
        )

    assert r.status_code == 200
    assert r.json()["estado"] == "pendiente"
    assert r.json()["motivo"] == "notificaciones_no_disponibles"


# ---------- KAN-290: datos del vehículo ----------

CONSULTA_VEHICULO = {
    "vehiculo_id": "hmac-de-la-placa",
    "placa": "ABC123",
    "consentimiento_id": "k-1",
    "fuente": "runt-vehiculo",
}


def test_vehiculo_con_proveedor_caido_degrada_sin_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/vehiculos/consultar", json=CONSULTA_VEHICULO)
        assert r.status_code == 200
        assert r.json()["degradado"] is True
        assert "vehiculos" in client.get("/circuitos").json()


def test_vehiculo_fuente_desconocida_responde_404() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/vehiculos/consultar", json={**CONSULTA_VEHICULO, "fuente": "simit"})
        assert r.status_code == 404


def test_asistencia_con_la_red_de_prestadores_caida_queda_no_confirmada_sin_5xx() -> None:
    # HU-M06 (v0.49): PRESTADORES_URL apunta a un puerto sin servicio.
    pedido = {
        "asistencia_id": "a-1", "intento": 1, "tipo_asistencia": "GRUA",
        "prestador_id": "prs-1", "prestador_latitud": 4.68, "prestador_longitud": -74.05,
        "latitud": 4.67, "longitud": -74.04,
    }  # fmt: skip
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/prestadores/asistencias", json=pedido)
        assert "prestadores" in client.get("/circuitos").json()

    assert r.status_code == 200
    assert r.json() == {
        "estado": "NO_CONFIRMADA",
        "minutos_estimados": None,
        "motivo": "PRESTADOR_SIN_RESPUESTA",
    }


# ---------- KAN-259 Fase 3: eventos paramétricos (clima y vuelos) ----------


def test_evento_parametrico_clima_con_proveedor_caido_no_publica_sin_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/eventos-parametricos/_consultar",
            json={
                "tipo": "CLIMA",
                "clave": "4.61,-74.08",
                "latitud": 4.61,
                "longitud": -74.08,
                "variable": "PRECIPITACION_MM",
            },
        )
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["publicado"] is False
        assert cuerpo["motivo"] == "proveedor_no_disponible"
        assert "clima" in client.get("/circuitos").json()


def test_evento_parametrico_vuelo_con_proveedor_caido_no_publica_sin_5xx() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post(
            "/eventos-parametricos/_consultar",
            json={"tipo": "VUELO", "numero_vuelo": "AV9340", "fecha": "2026-11-11"},
        )
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["publicado"] is False
        assert cuerpo["motivo"] == "proveedor_no_disponible"
        assert "vuelo" in client.get("/circuitos").json()


def test_evento_parametrico_clima_sin_campos_necesarios_es_422() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/eventos-parametricos/_consultar", json={"tipo": "CLIMA"})
        assert r.status_code == 422


def test_evento_parametrico_vuelo_sin_campos_necesarios_es_422() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/eventos-parametricos/_consultar", json={"tipo": "VUELO"})
        assert r.status_code == 422


def test_evento_parametrico_tipo_desconocido_es_422() -> None:
    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        r = client.post("/eventos-parametricos/_consultar", json={"tipo": "SISMO"})
        assert r.status_code == 422


def test_evento_parametrico_vuelo_con_fecha_hoy_consulta_la_fecha_de_bogota() -> None:
    """El Cloud Scheduler de demostración manda `fecha: "hoy"`; el ACL la resuelve en Bogotá."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    consultas = []

    class ServicioFalso:
        def consultar_vuelo(self, consulta):
            consultas.append(consulta)
            return None, "sin_dato"

    with TestClient(create_app(CONFIG_SIN_PROVEEDORES)) as client:
        client.app.state.servicio_eventos_parametricos = ServicioFalso()
        r = client.post(
            "/eventos-parametricos/_consultar",
            json={"tipo": "VUELO", "numero_vuelo": "AV9340", "fecha": "hoy"},
        )
    assert r.status_code == 200
    assert consultas[0].fecha == datetime.now(ZoneInfo("America/Bogota")).date().isoformat()
