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
                "clave_idempotencia": "idem-1",
                "poliza_id": "pol-1",
                "monto": "150000.00",
                "moneda": "COP",
                "token_medio_pago": "tok_abc123",
            },
        )
        assert r.status_code == 200
        assert r.json()["estado"] == "pendiente"
