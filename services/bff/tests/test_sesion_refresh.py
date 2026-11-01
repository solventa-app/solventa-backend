"""Sesión con refresh (T22): cookie httpOnly en la web, cuerpo en la app, CSRF y rotación."""

from fakes import AuthFalso, clientes_falsos
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import create_app

LOGIN = """
mutation { iniciarSesion(entrada: {usuario: "asesor.uno", clave: "clave-segura-1"}) {
  __typename ... on Sesion { token expiraEn refreshToken refreshExpiraEn }
} }
"""
RENOVAR = """
mutation($r: String) { renovarSesion(refreshToken: $r) {
  __typename ... on Sesion { token refreshToken refreshExpiraEn }
  ... on ErrorAutenticacion { mensaje codigo }
} }
"""
CERRAR = "mutation($r: String) { cerrarSesion(refreshToken: $r) }"
CSRF = {"X-Solventa-Csrf": "1"}


def _cliente(auth: AuthFalso | None = None, **config) -> tuple[TestClient, AuthFalso]:
    auth = auth or AuthFalso()
    configuracion = Configuracion.desde_entorno()
    if config:
        configuracion = Configuracion(**{**configuracion.__dict__, **config})
    app = create_app(config=configuracion, clientes=clientes_falsos(auth=auth))
    return TestClient(app), auth


def _post(client, canal, query, variables=None, headers=None, cookies=None):
    r = client.post(
        f"/graphql/{canal}",
        json={"query": query, "variables": variables or {}},
        headers=headers or {},
        cookies=cookies or {},
    )
    assert r.status_code == 200, r.text
    return r


def test_login_web_pone_la_cookie_httponly_y_no_entrega_el_refresh_al_js() -> None:
    client, _ = _cliente()
    with client:
        r = _post(client, "web", LOGIN)

    sesion = r.json()["data"]["iniciarSesion"]
    assert sesion["refreshToken"] is None and sesion["refreshExpiraEn"]
    cookie = r.headers["set-cookie"].lower()
    assert "solventa_refresh=refresh-1" in cookie
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie


def test_login_app_entrega_el_refresh_en_el_cuerpo_sin_cookie() -> None:
    client, _ = _cliente()
    with client:
        r = _post(client, "app", LOGIN)

    assert r.json()["data"]["iniciarSesion"]["refreshToken"] == "refresh-1"
    assert "set-cookie" not in r.headers


def test_renovar_web_con_cookie_rota_el_refresh_y_el_viejo_falla() -> None:
    client, _ = _cliente()
    with client:
        _post(client, "web", LOGIN)
        ok = _post(client, "web", RENOVAR, headers=CSRF, cookies={"solventa_refresh": "refresh-1"})
        reuso = _post(
            client, "web", RENOVAR, headers=CSRF, cookies={"solventa_refresh": "refresh-1"}
        )

    assert ok.json()["data"]["renovarSesion"]["__typename"] == "Sesion"
    assert "solventa_refresh=refresh-2" in ok.headers["set-cookie"]
    fallo = reuso.json()["data"]["renovarSesion"]
    assert fallo["codigo"] == "SESION_VENCIDA"
    assert "Max-Age=0" in reuso.headers["set-cookie"]  # la cookie muerta se borra


def test_renovar_web_sin_cabecera_csrf_o_con_origen_ajeno_se_rechaza() -> None:
    client, auth = _cliente(origenes_web=("https://app.solventa.co",))
    with client:
        _post(client, "web", LOGIN)
        sin_csrf = _post(client, "web", RENOVAR, cookies={"solventa_refresh": "refresh-1"})
        ajeno = _post(
            client,
            "web",
            RENOVAR,
            headers={**CSRF, "Origin": "https://malo.example"},
            cookies={"solventa_refresh": "refresh-1"},
        )
        propio = _post(
            client,
            "web",
            RENOVAR,
            headers={**CSRF, "Origin": "https://app.solventa.co"},
            cookies={"solventa_refresh": "refresh-1"},
        )

    assert sin_csrf.json()["data"]["renovarSesion"]["codigo"] == "SESION_VENCIDA"
    assert ajeno.json()["data"]["renovarSesion"]["codigo"] == "SESION_VENCIDA"
    assert propio.json()["data"]["renovarSesion"]["__typename"] == "Sesion"
    assert "refresh-1" not in auth.refrescos  # solo el intento válido llegó a AUTH


def test_renovar_web_sin_cookie_y_el_argumento_se_ignora_en_la_web() -> None:
    client, _ = _cliente()
    with client:
        _post(client, "web", LOGIN)
        r = _post(client, "web", RENOVAR, {"r": "refresh-1"}, headers=CSRF)

    assert r.json()["data"]["renovarSesion"]["codigo"] == "SESION_VENCIDA"


def test_renovar_app_por_argumento_y_refresh_de_otro_canal_no_sirve() -> None:
    client, _ = _cliente()
    with client:
        _post(client, "app", LOGIN)  # refresh-1 del canal app
        _post(client, "web", LOGIN)  # refresh-2 del canal web
        cruzado = _post(client, "app", RENOVAR, {"r": "refresh-2"})
        ok = _post(client, "app", RENOVAR, {"r": "refresh-1"})

    assert cruzado.json()["data"]["renovarSesion"]["codigo"] == "SESION_VENCIDA"
    sesion = ok.json()["data"]["renovarSesion"]
    assert sesion["__typename"] == "Sesion" and sesion["refreshToken"] == "refresh-3"
    assert "set-cookie" not in ok.headers


def test_cuenta_sin_verificar_renueva_en_la_app_pero_no_en_la_web() -> None:
    auth = AuthFalso()
    auth.usuarios["u-p"] = {**auth.usuarios["u-1"], "id": "u-p", "estado": "PENDIENTE_VERIFICACION"}
    auth.refrescos["rp-app"] = ("u-p", "app")
    auth.refrescos["rp-web"] = ("u-p", "web")
    client, _ = _cliente(auth)
    with client:
        app = _post(client, "app", RENOVAR, {"r": "rp-app"})
        web = _post(client, "web", RENOVAR, headers=CSRF, cookies={"solventa_refresh": "rp-web"})

    assert app.json()["data"]["renovarSesion"]["__typename"] == "Sesion"
    assert web.json()["data"]["renovarSesion"]["codigo"] == "SESION_VENCIDA"


def test_cerrar_sesion_revoca_y_borra_la_cookie() -> None:
    client, auth = _cliente()
    with client:
        _post(client, "web", LOGIN)
        sin_csrf = _post(client, "web", CERRAR, cookies={"solventa_refresh": "refresh-1"})
        r = _post(client, "web", CERRAR, headers=CSRF, cookies={"solventa_refresh": "refresh-1"})

    assert sin_csrf.json()["data"]["cerrarSesion"] is False
    assert r.json()["data"]["cerrarSesion"] is True
    assert auth.refrescos_cerrados == ["refresh-1"]
    assert "Max-Age=0" in r.headers["set-cookie"]


def test_un_fallo_de_auth_no_es_un_5xx_ni_borra_la_cookie() -> None:
    client, _ = _cliente(AuthFalso(falla=True))
    with client:
        r = _post(client, "web", RENOVAR, headers=CSRF, cookies={"solventa_refresh": "refresh-1"})

    cuerpo = r.json()
    assert cuerpo["errors"][0]["message"] == "No pudimos renovar tu sesión. Intenta de nuevo."
    assert "set-cookie" not in r.headers
