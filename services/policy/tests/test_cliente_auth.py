"""`ClienteAuth` de POLICY contra respuestas HTTP simuladas (sin levantar AUTH)."""

import json
import logging

import httpx
import pytest

from app.application.cliente_auth import AuthNoDisponible, ClienteAuth, Contacto

CORREO = "maria.gomez@ejemplo.co"


def _http(responder, vistas: list[httpx.Request] | None = None) -> ClienteAuth:
    def manejar(peticion: httpx.Request) -> httpx.Response:
        if vistas is not None:
            vistas.append(peticion)
        return responder(peticion)

    cliente = ClienteAuth("http://auth", 1.0)
    cliente._cliente = httpx.Client(base_url="http://auth", transport=httpx.MockTransport(manejar))
    return cliente


def test_el_contacto_se_pide_por_la_ruta_privada_de_auth() -> None:
    vistas: list[httpx.Request] = []

    contacto = _http(
        lambda _: httpx.Response(200, json={"correo": CORREO, "telefono": None}), vistas
    ).contacto_de("u-1")

    assert contacto == Contacto(correo=CORREO, telefono=None)
    assert (vistas[0].method, vistas[0].url.path) == ("GET", "/usuarios/u-1/contacto")


def test_un_404_con_el_mensaje_de_auth_es_un_cliente_que_no_conoce_no_una_falla() -> None:
    respuesta = httpx.Response(404, json={"detail": "usuario no encontrado"})

    assert _http(lambda _: respuesta).contacto_de("u-1") is None


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(404, json={"detail": "Not Found"}),  # un AUTH que aún no tiene la ruta
        httpx.Response(404),
        httpx.Response(404, content=b"<html>no es de AUTH</html>"),
    ],
)
def test_otro_404_no_se_toma_por_cliente_sin_contacto(respuesta) -> None:
    """Omitir el aviso es permanente: un 404 que no es de AUTH (despliegue gradual, un proxy) se
    reintenta en vez de dejar a los clientes sin su póliza."""
    with pytest.raises(AuthNoDisponible):
        _http(lambda _: respuesta).contacto_de("u-1")


def test_un_contacto_vacio_se_reconoce_como_vacio() -> None:
    contacto = _http(
        lambda _: httpx.Response(200, json={"correo": None, "telefono": None})
    ).contacto_de("u-1")

    assert contacto.vacio is True
    assert Contacto(correo="", telefono=None).vacio is True
    assert Contacto(telefono="+573001234567").vacio is False


@pytest.mark.parametrize(
    "respuesta",
    [
        lambda _: httpx.Response(500),
        lambda _: httpx.Response(503),
        lambda _: httpx.Response(200, content=b"no es json"),
        lambda _: httpx.Response(200, json=["no", "es", "un", "objeto"]),
    ],
)
def test_si_auth_falla_no_se_asume_que_no_hay_contacto(respuesta) -> None:
    with pytest.raises(AuthNoDisponible):
        _http(respuesta).contacto_de("u-1")


def test_si_la_red_falla_tambien_es_auth_no_disponible() -> None:
    def cae(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red")

    with pytest.raises(AuthNoDisponible):
        _http(cae).contacto_de("u-1")


def test_el_mensaje_del_error_no_lleva_la_respuesta() -> None:
    """La respuesta de AUTH es dato personal: el error solo dice el tipo."""
    with pytest.raises(AuthNoDisponible) as error:
        _http(lambda _: httpx.Response(200, content=f"{CORREO} no es json".encode())).contacto_de(
            "u-1"
        )

    assert CORREO not in str(error.value) and "ejemplo" not in repr(error.value)


def test_el_nombre_se_pide_con_el_id_en_el_cuerpo_y_no_en_la_url() -> None:
    vistas: list[httpx.Request] = []

    nombre = _http(
        lambda _: httpx.Response(200, json={"u-1": "María Fernanda Gómez"}), vistas
    ).nombre_de("u-1")

    assert nombre == "María Fernanda Gómez"
    assert (vistas[0].method, vistas[0].url.path) == ("POST", "/usuarios/nombres")
    assert json.loads(vistas[0].content) == {"ids": ["u-1"]}
    assert "u-1" not in str(vistas[0].url)


def test_un_cliente_sin_nombre_da_none() -> None:
    assert _http(lambda _: httpx.Response(200, json={})).nombre_de("u-1") is None


def test_si_auth_falla_al_pedir_el_nombre_es_auth_no_disponible() -> None:
    with pytest.raises(AuthNoDisponible):
        _http(lambda _: httpx.Response(503)).nombre_de("u-1")


def test_el_cliente_no_escribe_en_los_logs(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        _http(
            lambda _: httpx.Response(200, json={"correo": CORREO, "telefono": "+573001234567"})
        ).contacto_de("u-1")
        _http(lambda _: httpx.Response(200, json={"u-1": "María Fernanda Gómez"})).nombre_de("u-1")

    for dato in (CORREO, "3001234567", "María", "Gómez"):
        assert dato not in caplog.text
