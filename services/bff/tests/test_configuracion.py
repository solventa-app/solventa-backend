import pytest

from app.configuracion import (
    CLAVE_CLIENTE_ID_LOCAL,
    JWT_SECRETO_LOCAL,
    VALOR_MARCADOR,
    Configuracion,
)


def test_en_local_usa_la_clave_local(monkeypatch) -> None:
    monkeypatch.delenv("AUTENTICACION_SERVICIOS", raising=False)
    monkeypatch.delenv("CLIENTE_ID_CLAVE", raising=False)

    assert Configuracion.desde_entorno().cliente_id_clave == CLAVE_CLIENTE_ID_LOCAL


@pytest.mark.parametrize("clave", [None, CLAVE_CLIENTE_ID_LOCAL, VALOR_MARCADOR])
def test_en_la_nube_no_arranca_sin_la_clave_real(monkeypatch, clave) -> None:
    monkeypatch.setenv("AUTENTICACION_SERVICIOS", "id_token")
    if clave is None:
        monkeypatch.delenv("CLIENTE_ID_CLAVE", raising=False)
    else:
        monkeypatch.setenv("CLIENTE_ID_CLAVE", clave)

    with pytest.raises(ValueError):
        Configuracion.desde_entorno()


def test_en_la_nube_arranca_con_la_clave_real(monkeypatch) -> None:
    monkeypatch.setenv("AUTENTICACION_SERVICIOS", "id_token")
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "a" * 64)
    monkeypatch.setenv("JWT_SECRETO", "b" * 64)

    config = Configuracion.desde_entorno()

    assert config.cliente_id_clave == "a" * 64
    assert config.jwt_secreto == "b" * 64


@pytest.mark.parametrize("secreto", [JWT_SECRETO_LOCAL, VALOR_MARCADOR, "corto"])
def test_en_la_nube_no_arranca_sin_un_jwt_secreto_real(monkeypatch, secreto) -> None:
    monkeypatch.setenv("AUTENTICACION_SERVICIOS", "id_token")
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "a" * 64)
    monkeypatch.setenv("JWT_SECRETO", secreto)

    with pytest.raises(ValueError):
        Configuracion.desde_entorno()


def test_modo_de_autenticacion_por_defecto_y_desconocido(monkeypatch) -> None:
    monkeypatch.delenv("AUTENTICACION_SERVICIOS", raising=False)
    monkeypatch.delenv("AUTENTICACION_USUARIOS", raising=False)
    assert Configuracion.desde_entorno().autenticacion_usuarios == "opcional"

    monkeypatch.setenv("AUTENTICACION_USUARIOS", "a-veces")
    with pytest.raises(ValueError):
        Configuracion.desde_entorno()
