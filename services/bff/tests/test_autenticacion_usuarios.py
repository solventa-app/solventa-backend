"""Lectura y verificación del JWT que emite AUTH."""

import pytest
from fakes import token_de_prueba

from app.autenticacion_usuarios import (
    TokenInvalido,
    extraer_cabecera_autorizacion,
    leer_usuario,
)
from app.configuracion import JWT_SECRETO_LOCAL


def test_sin_cabecera_no_hay_usuario() -> None:
    assert leer_usuario(None, JWT_SECRETO_LOCAL) is None
    assert leer_usuario("", JWT_SECRETO_LOCAL) is None


def test_extraer_cabecera_prefiere_x_forwarded_authorization_del_gateway() -> None:
    token_usuario = f"Bearer {token_de_prueba('CLIENTE', 'u-cliente')}"
    cabeceras = {
        "authorization": "Bearer token-oid-del-gateway-espv2",
        "x-forwarded-authorization": token_usuario,
    }
    extraida = extraer_cabecera_autorizacion(cabeceras)
    usuario = leer_usuario(extraida, JWT_SECRETO_LOCAL)

    assert extraida == token_usuario
    assert (usuario.id, usuario.rol) == ("u-cliente", "CLIENTE")


def test_extraer_cabecera_cae_a_authorization_en_local_y_envoy() -> None:
    token_usuario = f"Bearer {token_de_prueba('ASESOR', 'u-asesor')}"
    cabeceras = {"authorization": token_usuario}
    extraida = extraer_cabecera_autorizacion(cabeceras)
    usuario = leer_usuario(extraida, JWT_SECRETO_LOCAL)

    assert extraida == token_usuario
    assert (usuario.id, usuario.rol) == ("u-asesor", "ASESOR")


def test_token_valido_devuelve_el_usuario() -> None:
    usuario = leer_usuario(f"Bearer {token_de_prueba('ASESOR', 'u-9')}", JWT_SECRETO_LOCAL)

    assert (usuario.id, usuario.rol) == ("u-9", "ASESOR")


@pytest.mark.parametrize(
    "cabecera",
    [
        "Basic dXN1YXJpbzpjbGF2ZQ==",
        "Bearer",
        "Bearer no-es-un-jwt",
        f"Bearer {token_de_prueba('ASESOR', secreto='otro-secreto-de-32-bytes-o-mas-xx')}",
        f"Bearer {token_de_prueba('ASESOR', segundos=-10)}",
        f"Bearer {token_de_prueba('ADMIN')}",
    ],
    ids=["otro-esquema", "vacio", "malformado", "firma-ajena", "vencido", "rol-desconocido"],
)
def test_token_invalido(cabecera: str) -> None:
    with pytest.raises(TokenInvalido):
        leer_usuario(cabecera, JWT_SECRETO_LOCAL)


def test_token_alterado_no_pasa() -> None:
    token = token_de_prueba("CLIENTE")
    cabeza, cuerpo, firma = token.split(".")
    alterado = f"{cabeza}.{cuerpo}.{firma[:-2]}xx"

    with pytest.raises(TokenInvalido):
        leer_usuario(f"Bearer {alterado}", JWT_SECRETO_LOCAL)
