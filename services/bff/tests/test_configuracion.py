import pytest

from app.configuracion import CLAVE_CLIENTE_ID_LOCAL, VALOR_MARCADOR, Configuracion


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

    assert Configuracion.desde_entorno().cliente_id_clave == "a" * 64
