"""cobros: id_intento_cobro reemplaza a clave_idempotencia, fase de expansión (KAN-28)

Expandir/contraer (ADR-07): se agrega `id_intento_cobro` con el mismo valor y su índice único,
mientras `clave_idempotencia` sigue existiendo para la revisión anterior de la app. La columna
nueva queda nullable porque la revisión anterior inserta sin ella; `0004` la rellena de nuevo,
la vuelve obligatoria y borra la vieja.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cobros", sa.Column("id_intento_cobro", sa.Text(), nullable=True))
    op.execute("UPDATE cobros SET id_intento_cobro = clave_idempotencia")
    op.create_unique_constraint("uq_cobros_id_intento_cobro", "cobros", ["id_intento_cobro"])


def downgrade() -> None:
    op.drop_constraint("uq_cobros_id_intento_cobro", "cobros", type_="unique")
    op.drop_column("cobros", "id_intento_cobro")
