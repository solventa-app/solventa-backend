"""Entorno de Alembic de PAYMENTS.

Las migraciones son SQL explícito (sin ORM ni autogenerate): el esquema lo definen ellas, no los
modelos. El comando es el mismo en local, CI y staging: `alembic upgrade head` (en staging, como
Cloud Run Job con la misma imagen que se va a desplegar).
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

fileConfig(context.config.config_file_name)

# Serializa ejecuciones simultáneas (reintento del job, dos despliegues a la vez).
# El bloqueo es de sesión: se libera al cerrar la conexión.
CLAVE_BLOQUEO = 7_100_002


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DATABASE_URL")
    # DATABASE_URL usa el esquema genérico; SQLAlchemy necesita nombrar el driver.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def migrar_en_linea() -> None:
    motor = create_engine(_url(), poolclass=pool.NullPool)
    with motor.connect() as conexion:
        conexion.execute(text("SELECT pg_advisory_lock(:clave)"), {"clave": CLAVE_BLOQUEO})
        conexion.commit()
        context.configure(connection=conexion, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Solo se admite el modo en línea: se aplican contra la base.")
migrar_en_linea()
