"""Token de identidad hacia los servicios privados de Cloud Run (sin red: dobles)."""

import httpx
import pytest

from app.application.autenticacion_servicios import (
    VIGENCIA_SEGUNDOS,
    AutenticacionIdToken,
    autenticacion_para,
    modo_desde_entorno,
)


def _cliente_que_captura(auth: httpx.Auth, vistas: list[httpx.Request]) -> httpx.Client:
    def responder(peticion: httpx.Request) -> httpx.Response:
        vistas.append(peticion)
        return httpx.Response(200, json={})

    transporte = httpx.MockTransport(responder)
    return httpx.Client(base_url="http://destino", transport=transporte, auth=auth)


def test_manda_el_token_y_lo_reutiliza_hasta_que_vence() -> None:
    pedidos: list[str] = []
    ahora = [0.0]
    auth = AutenticacionIdToken(
        "acl-worker",
        pedir_token=lambda aud: pedidos.append(aud) or f"token-{len(pedidos)}",
        reloj=lambda: ahora[0],
    )
    vistas: list[httpx.Request] = []
    with _cliente_que_captura(auth, vistas) as cliente:
        cliente.get("/a")
        cliente.get("/b")
        ahora[0] = VIGENCIA_SEGUNDOS + 1
        cliente.get("/c")

    assert pedidos == ["acl-worker", "acl-worker"]
    assert [v.headers["Authorization"] for v in vistas] == [
        "Bearer token-1",
        "Bearer token-1",
        "Bearer token-2",
    ]


def test_en_local_no_agrega_nada() -> None:
    assert autenticacion_para("ninguna", "acl-worker") is None
    assert isinstance(autenticacion_para("id_token", "acl-worker"), AutenticacionIdToken)
    with pytest.raises(ValueError):
        autenticacion_para("otro", "acl-worker")


def test_el_modo_se_lee_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTENTICACION_SERVICIOS", raising=False)
    assert modo_desde_entorno() == "ninguna"
    monkeypatch.setenv("AUTENTICACION_SERVICIOS", "id_token")
    assert modo_desde_entorno() == "id_token"
    monkeypatch.setenv("AUTENTICACION_SERVICIOS", "otro")
    with pytest.raises(ValueError):
        modo_desde_entorno()


def test_cliente_acl_manda_el_token_al_acl_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application.cliente_acl import ClienteAcl

    vistas: list[httpx.Request] = []
    auth = AutenticacionIdToken("acl-worker", pedir_token=lambda aud: "t-acl")
    cliente = ClienteAcl("http://acl", 1.0, auth)
    cliente._cliente = _cliente_que_captura(auth, vistas)  # mismo `auth`, transporte simulado

    cliente.consultar("intento-1")

    assert vistas[0].headers["Authorization"] == "Bearer t-acl"
