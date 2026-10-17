"""cobros: reintentos manuales de un cobro rechazado (KAN-28, CA-W05-02)

Decisión del equipo (2026-10-17, prototipo W05-01): un cobro `rechazado` se puede reintentar
a mano hasta 3 veces. La pasarela es idempotente por referencia, así que cada reintento usa
su propia referencia (`{id_intento_cobro}-r1`, `-r2`, `-r3`):

- `reintentos_rechazo`: cuántos reintentos manuales de un rechazado se hicieron.
- `referencia_pasarela`: la referencia del intento vigente ante la pasarela; nula mientras
  sea la original (`id_intento_cobro`).

Solo agrega (expandir, ADR-07): la revisión anterior no conoce las columnas y no las toca.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cobros",
        sa.Column("reintentos_rechazo", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("cobros", sa.Column("referencia_pasarela", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cobros", "referencia_pasarela")
    op.drop_column("cobros", "reintentos_rechazo")
