"""La URL de la base no tiene valor por defecto: la fija el entorno (compose, Cloud Run)."""

import pytest

from app.application.configuracion import Configuracion
from app.application.repositorio_cobros import construir_pool


def test_sin_database_url_no_hay_valor_por_defecto_y_el_pool_falla_claro(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert Configuracion.desde_entorno().database_url == ""
    with pytest.raises(ValueError, match="DATABASE_URL"):
        construir_pool("")


def test_toma_la_url_del_entorno(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://servidor/payments")

    assert Configuracion.desde_entorno().database_url == "postgresql://servidor/payments"
