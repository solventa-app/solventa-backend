"""cobros: borra clave_idempotencia, fase de contracción (KAN-28)

Rellena `id_intento_cobro` en las filas que insertó la revisión anterior de la app, lo vuelve
obligatorio y borra `clave_idempotencia`. El `downgrade` vuelve a crear la columna vieja con el
mismo valor.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE cobros SET id_intento_cobro = clave_idempotencia WHERE id_intento_cobro IS NULL"
    )
    op.alter_column("cobros", "id_intento_cobro", nullable=False)
    op.drop_column("cobros", "clave_idempotencia")


def downgrade() -> None:
    op.add_column("cobros", sa.Column("clave_idempotencia", sa.Text(), nullable=True))
    op.execute("UPDATE cobros SET clave_idempotencia = id_intento_cobro")
    op.alter_column("cobros", "clave_idempotencia", nullable=False)
    op.create_unique_constraint("cobros_clave_idempotencia_key", "cobros", ["clave_idempotencia"])
    op.alter_column("cobros", "id_intento_cobro", nullable=True)
