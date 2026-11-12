import base64
import json

from fakes import AuthFalso, BusEventosFalso
from fastapi.testclient import TestClient

from app.clientes import ErrorServicio
from app.main import create_app
from app.plantillas import Plantillas
from app.servicio import ServicioEnviarPush
from app.sns import EnviadorStub


def _destino() -> dict:
    return {"dispositivo_id": "d1", "token": "tok-1", "plataforma": "android"}


def _cliente(auth: AuthFalso | None = None, bus: BusEventosFalso | None = None) -> TestClient:
    auth = auth if auth is not None else AuthFalso(destinos={"u1": _destino()})
    servicio = ServicioEnviarPush(auth, EnviadorStub(), Plantillas())
    bus = bus if bus is not None else BusEventosFalso()
    return TestClient(create_app(servicio=servicio, bus=bus))


def test_health() -> None:
    with _cliente() as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["estado"] == "ok"


def test_v1_push_publica_en_el_bus() -> None:
    bus = BusEventosFalso()
    with _cliente(bus=bus) as client:
        r = client.post(
            "/v1/push",
            json={"idempotencia": "idem-1", "usuarioId": "u1", "plantilla": "sesion-cerrada"},
        )
        assert r.status_code == 202
        assert "solicitudId" in r.json()
        assert len(bus.eventos) == 1
        assert bus.eventos[0]["datos"]["usuarioId"] == "u1"


def test_v1_push_rechaza_solicitud_invalida() -> None:
    with _cliente() as client:
        r = client.post("/v1/push", json={"idempotencia": "", "usuarioId": "u1", "plantilla": "x"})
        assert r.status_code == 422  # pydantic: idempotencia con min_length=1


def _evento_directo() -> dict:
    return {
        "eventId": "9d7b8f5e-9c2a-4e3e-8a4b-6a9b8e5a1c01",
        "tipo": "notificacion.push-solicitada",
        "version": 1,
        "ocurridoEn": "2026-11-11T00:00:00+00:00",
        "datos": {"idempotencia": "idem-2", "usuarioId": "u1", "plantilla": "sesion-cerrada"},
    }


def test_eventos_acepta_el_evento_directo() -> None:
    with _cliente() as client:
        r = client.post("/eventos", json=_evento_directo())
        assert r.status_code == 200
        assert r.json()["resultado"] == "ENVIADO"


def test_eventos_acepta_el_sobre_de_pubsub() -> None:
    data_b64 = base64.b64encode(json.dumps(_evento_directo()).encode("utf-8")).decode("ascii")
    with _cliente() as client:
        r = client.post("/eventos", json={"message": {"data": data_b64}})
        assert r.status_code == 200
        assert r.json()["resultado"] == "ENVIADO"


def test_eventos_sobre_de_pubsub_con_data_invalida() -> None:
    with _cliente() as client:
        r = client.post("/eventos", json={"message": {"data": "no-es-base64-valido!!"}})
        assert r.status_code == 400


def test_eventos_rechaza_evento_invalido() -> None:
    with _cliente() as client:
        r = client.post("/eventos", json={"eventId": "e1"})
        assert r.status_code == 400


def test_eventos_503_si_auth_no_responde() -> None:
    class _AuthCaida(AuthFalso):
        async def destino_push(self, usuario_id: str) -> dict:
            raise ErrorServicio("ConnectError")

    with _cliente(auth=_AuthCaida()) as client:
        r = client.post("/eventos", json=_evento_directo())
        assert r.status_code == 503


def test_metrics_expuestas() -> None:
    with _cliente() as client:
        assert client.get("/metrics").status_code == 200
