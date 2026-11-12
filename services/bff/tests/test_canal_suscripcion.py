"""`/v1/graphql/web-suscripcion` sirve el mismo esquema y el mismo contexto que el canal web.

Solo cambia el plazo que le da el gateway (5 s en lugar de 700 ms) para firmar, pagar y emitir sin
perder la respuesta. Con dobles, sin red."""

from fakes import AuthFalso, PolicyFalso, RatingFalso, clientes_falsos, token_de_prueba
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import RUTA_SUSCRIPCION, create_app

CONSULTA = "{ yo { rol } }"


def _cliente() -> TestClient:
    clientes = clientes_falsos(auth=AuthFalso(), rating=RatingFalso(), policy=PolicyFalso())
    return TestClient(create_app(config=Configuracion.desde_entorno(), clientes=clientes))


def _post(client: TestClient, ruta: str, rol: str = "CLIENTE"):
    return client.post(
        ruta,
        json={"query": CONSULTA},
        headers={"Authorization": f"Bearer {token_de_prueba(rol, usuario_id='u-1')}"},
    )


def test_la_ruta_de_suscripcion_responde_igual_que_el_canal_web() -> None:
    with _cliente() as client:
        web = _post(client, "/v1/graphql/web")
        suscripcion = _post(client, f"/v1/graphql/{RUTA_SUSCRIPCION}")

    assert web.status_code == suscripcion.status_code == 200
    assert suscripcion.json() == web.json()
    assert "errors" not in suscripcion.json()
    assert suscripcion.json()["data"]["yo"]["rol"] is not None


def test_la_ruta_de_suscripcion_pide_sesion_como_el_canal_web() -> None:
    with _cliente() as client:
        sin_token = client.post(f"/v1/graphql/{RUTA_SUSCRIPCION}", json={"query": CONSULTA})
        web = client.post("/v1/graphql/web", json={"query": CONSULTA})

    assert sin_token.status_code == web.status_code
    assert sin_token.json() == web.json()


def test_la_ruta_se_llama_web_suscripcion() -> None:
    # El gateway (openapi.yaml y envoy.yaml) y el cliente web dependen de este nombre.
    assert RUTA_SUSCRIPCION == "web-suscripcion"
