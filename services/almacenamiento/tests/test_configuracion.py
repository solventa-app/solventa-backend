import pytest

from app.application.configuracion import Configuracion


def test_valores_por_defecto_en_local(monkeypatch: pytest.MonkeyPatch) -> None:
    for nombre in ("K_SERVICE", "CLOUD_RUN_JOB", "FIRMANTE", "ALMACEN_BUCKET"):
        monkeypatch.delenv(nombre, raising=False)
    config = Configuracion.desde_entorno()
    assert config.firmante == "local"
    assert config.bucket == ""
    assert (config.vigencia_subida_s, config.vigencia_descarga_s) == (600, 300)


def test_en_la_nube_firma_con_iam_y_no_acepta_el_firmante_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K_SERVICE", "almacenamiento")
    monkeypatch.delenv("FIRMANTE", raising=False)
    assert Configuracion.desde_entorno().firmante == "iam"
    monkeypatch.setenv("FIRMANTE", "local")
    with pytest.raises(ValueError):
        Configuracion.desde_entorno()


@pytest.mark.parametrize(
    ("nombre", "valor"),
    [
        ("FIRMANTE", "otro"),
        ("URL_SUBIDA_VIGENCIA_SEGUNDOS", "0"),
        ("URL_DESCARGA_VIGENCIA_SEGUNDOS", "x"),
    ],
)
def test_valores_invalidos(monkeypatch: pytest.MonkeyPatch, nombre: str, valor: str) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv(nombre, valor)
    with pytest.raises(ValueError):
        Configuracion.desde_entorno()
