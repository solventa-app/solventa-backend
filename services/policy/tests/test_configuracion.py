import pytest

from app.application.configuracion import Configuracion


def _limpiar(monkeypatch) -> None:
    for nombre in (
        "DISPARADOR", "GCP_PROYECTO", "GCP_REGION", "TAREAS_CUENTA", "URL_PROPIA",
        "TOKEN_RECAUDO_CREDITO", "K_SERVICE", "CLOUD_RUN_JOB",
    ):  # fmt: skip
        monkeypatch.delenv(nombre, raising=False)


def test_los_valores_por_defecto_son_los_de_local(monkeypatch) -> None:
    _limpiar(monkeypatch)

    config = Configuracion.desde_entorno()

    assert config.disparador == "bucle"
    assert config.tareas_cola == "emision-polizas"
    assert config.under_url == "http://localhost:8008"
    assert config.payments_url == "http://localhost:8004"
    assert config.emision_max_intentos == 12


def test_un_disparador_desconocido_se_rechaza(monkeypatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("DISPARADOR", "cron")

    with pytest.raises(ValueError, match="DISPARADOR"):
        Configuracion.desde_entorno()


def test_el_disparador_externo_exige_lo_que_cloud_tasks_necesita(monkeypatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("DISPARADOR", "externo")

    with pytest.raises(ValueError, match="GCP_PROYECTO"):
        Configuracion.desde_entorno()

    for nombre, valor in (
        ("GCP_PROYECTO", "p"), ("GCP_REGION", "r"), ("TAREAS_CUENTA", "c"), ("URL_PROPIA", "u"),
    ):  # fmt: skip
        monkeypatch.setenv(nombre, valor)
    assert Configuracion.desde_entorno().disparador == "externo"


def test_en_la_nube_el_token_de_recaudo_no_puede_ser_el_local(monkeypatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("K_SERVICE", "policy")

    with pytest.raises(ValueError, match="TOKEN_RECAUDO_CREDITO"):
        Configuracion.desde_entorno()

    monkeypatch.setenv("TOKEN_RECAUDO_CREDITO", "tok_recaudo_staging")
    assert Configuracion.desde_entorno().token_recaudo_credito == "tok_recaudo_staging"
