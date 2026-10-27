import pytest

from app.application.configuracion import Configuracion


def test_en_la_nube_exige_la_clave_del_titular_y_cloud_tasks(monkeypatch) -> None:
    monkeypatch.setenv("K_SERVICE", "onboarding")
    monkeypatch.setenv("DISPARADOR", "externo")
    with pytest.raises(ValueError, match="DISPARADOR=externo"):
        Configuracion.desde_entorno()
    for nombre in ("GCP_PROYECTO", "TAREAS_CUENTA", "URL_PROPIA"):
        monkeypatch.setenv(nombre, "x")
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "pendiente-de-cargar")
    with pytest.raises(ValueError, match="CLIENTE_ID_CLAVE"):
        Configuracion.desde_entorno()
    monkeypatch.setenv("CLIENTE_ID_CLAVE", "clave-real")
    config = Configuracion.desde_entorno()
    assert (config.disparador, config.edad_minima, config.max_intentos) == ("externo", 18, 3)


def test_sin_disparador_corre_con_un_hilo(monkeypatch) -> None:
    monkeypatch.delenv("DISPARADOR", raising=False)
    assert Configuracion.desde_entorno().disparador == "bucle"
