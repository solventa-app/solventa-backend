"""Migración 0007: cifra los tokens que quedaron en claro (idempotente y reversible).

Se prueba con SQLite en memoria: la migración usa solo SQL estándar. La verificación contra el
Postgres real (subir, bajar y volver a subir) la hace `scripts/verificar-migraciones.sh
payments`."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from app.application.cifrado import PREFIJO, cifrador_desde_entorno

RUTA = Path(__file__).parent.parent / "migrations" / "versions" / "0007_cifra_token_medio_pago.py"


@pytest.fixture
def migracion():
    especificacion = importlib.util.spec_from_file_location("migracion_0007", RUTA)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


@pytest.fixture(autouse=True)
def _llave_local(monkeypatch):
    for nombre in ("K_SERVICE", "CLOUD_RUN_JOB", "CIFRADO_CLAVE"):
        monkeypatch.delenv(nombre, raising=False)


def _con_filas():
    motor = create_engine("sqlite://")
    with motor.begin() as conexion:
        conexion.execute(text("CREATE TABLE cobros (id TEXT PRIMARY KEY, token_medio_pago TEXT)"))
        conexion.execute(text("INSERT INTO cobros VALUES ('c1', 'tok_visa_0001')"))
        conexion.execute(text("INSERT INTO cobros VALUES ('c2', 'tok_mc_0002')"))
    return motor


def _tokens(motor) -> dict[str, str]:
    with motor.connect() as conexion:
        return dict(conexion.execute(text("SELECT id, token_medio_pago FROM cobros")).fetchall())


def _ejecutar(motor, paso) -> None:
    with motor.begin() as conexion:
        with Operations.context(MigrationContext.configure(conexion)):
            paso()


def test_sube_cifrando_lo_que_estaba_en_claro_y_es_idempotente(migracion) -> None:
    motor = _con_filas()

    _ejecutar(motor, migracion.upgrade)
    primera = _tokens(motor)
    _ejecutar(motor, migracion.upgrade)

    assert all(v.startswith(PREFIJO) for v in primera.values())
    assert "tok_visa_0001" not in primera["c1"]
    assert _tokens(motor) == primera  # la segunda vez no cambia nada
    cifrador = cifrador_desde_entorno()
    assert cifrador.descifrar(primera["c1"], "c1") == "tok_visa_0001"


def test_baja_dejando_las_filas_en_claro(migracion) -> None:
    motor = _con_filas()
    _ejecutar(motor, migracion.upgrade)

    _ejecutar(motor, migracion.downgrade)

    assert _tokens(motor) == {"c1": "tok_visa_0001", "c2": "tok_mc_0002"}
