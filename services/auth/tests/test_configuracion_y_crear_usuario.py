"""Configuración (secreto en la nube) y tarea de arranque del primer BACKOFFICE."""

import pytest
from fakes import RepositorioUsuariosMemoria

from app.configuracion import JWT_SECRETO_LOCAL, VALOR_MARCADOR, Configuracion
from app.crear_usuario import crear_si_no_existe
from app.usuarios import construir_pool


def test_en_local_usa_el_secreto_local(monkeypatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("JWT_SECRETO", raising=False)

    assert Configuracion.desde_entorno().jwt_secreto == JWT_SECRETO_LOCAL


@pytest.mark.parametrize("secreto", [None, JWT_SECRETO_LOCAL, VALOR_MARCADOR])
def test_en_cloud_run_no_arranca_sin_el_secreto_real(monkeypatch, secreto) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    if secreto is None:
        monkeypatch.delenv("JWT_SECRETO", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRETO", secreto)

    with pytest.raises(ValueError):
        Configuracion.desde_entorno()


def test_en_cloud_run_rechaza_un_secreto_corto(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", "corto")

    with pytest.raises(ValueError, match="32 bytes"):
        Configuracion.desde_entorno()


def test_en_cloud_run_arranca_con_un_secreto_real(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", "a" * 64)
    monkeypatch.setenv("CIFRADO_CLAVE", "b" * 43 + "=")
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "clave-titular-real")
    monkeypatch.setenv("SOCIOS_CLAVE_FIRMA", "clave-de-firma-de-socios-real")

    assert Configuracion.desde_entorno().jwt_secreto == "a" * 64


@pytest.mark.parametrize("valor", [None, "pendiente-de-cargar"])
def test_en_cloud_run_no_arranca_sin_la_clave_de_firma_de_socios(monkeypatch, valor) -> None:
    # W07-26: en la nube nunca se firman tokens de socios con la clave local, que es pública.
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", "a" * 64)
    monkeypatch.setenv("CIFRADO_CLAVE", "b" * 43 + "=")
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "clave-titular-real")
    if valor is None:
        monkeypatch.delenv("SOCIOS_CLAVE_FIRMA", raising=False)
    else:
        monkeypatch.setenv("SOCIOS_CLAVE_FIRMA", valor)

    with pytest.raises(ValueError, match="SOCIOS_CLAVE_FIRMA"):
        Configuracion.desde_entorno()


@pytest.mark.parametrize("valor", [None, "pendiente-de-cargar"])
def test_en_cloud_run_no_arranca_sin_la_clave_del_titular(monkeypatch, valor) -> None:
    # Con la clave local, el titular del usuario no coincidiría con el del consentimiento (W03-27).
    monkeypatch.setenv("K_SERVICE", "auth")
    monkeypatch.setenv("JWT_SECRETO", "a" * 64)
    monkeypatch.setenv("CIFRADO_CLAVE", "b" * 43 + "=")
    if valor is None:
        monkeypatch.delenv("CLIENTE_ID_CLAVE", raising=False)
    else:
        monkeypatch.setenv("CLIENTE_ID_CLAVE", valor)

    with pytest.raises(ValueError, match="CLIENTE_ID_CLAVE"):
        Configuracion.desde_entorno()


def test_sin_database_url_no_hay_valor_por_defecto_y_el_pool_falla_claro(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert Configuracion.desde_entorno().database_url == ""
    with pytest.raises(ValueError, match="DATABASE_URL"):
        construir_pool("")


def test_crear_usuario_es_idempotente() -> None:
    repositorio = RepositorioUsuariosMemoria()

    primera = crear_si_no_existe(repositorio, "admin.solventa", "clave-segura-1", "BACKOFFICE")
    segunda = crear_si_no_existe(repositorio, "admin.solventa", "clave-segura-1", "BACKOFFICE")

    assert (primera, segunda) == (True, False)
    assert len(repositorio.filas) == 1
