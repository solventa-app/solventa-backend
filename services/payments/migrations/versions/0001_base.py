"""base: punto de partida del esquema de PAYMENTS

Sin tablas a propósito: los estados de cobro, la idempotencia y los balances llegan con
T-W05-3 / KAN-28 en su propia migración. Esta existe para que el camino migrar -> desplegar se
pruebe de extremo a extremo desde el primer día.
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
