from app.configuracion import Configuracion


def test_valores_locales_por_defecto(monkeypatch) -> None:
    for nombre in ("REDIS_URL", "ORIGENES_PERMITIDOS", "LATIDO_SEGUNDOS"):
        monkeypatch.delenv(nombre, raising=False)

    config = Configuracion.desde_entorno()

    assert config.redis_url == "redis://localhost:6379/3"
    assert "http://localhost:5173" in config.origenes_permitidos
    assert config.latido_segundos == 25


def test_los_origenes_se_normalizan(monkeypatch) -> None:
    monkeypatch.setenv(
        "ORIGENES_PERMITIDOS", " https://app.solventa.co/ , https://web.solventa.co "
    )

    assert Configuracion.desde_entorno().origenes_permitidos == {
        "https://app.solventa.co",
        "https://web.solventa.co",
    }
