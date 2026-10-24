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
