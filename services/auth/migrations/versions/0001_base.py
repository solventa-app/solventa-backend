"""base: punto de partida del esquema de AUTH

Sin tablas a propósito: la tabla de consentimiento (append-only) llega con T-W01-4 / KAN-24 en su
propia migración. Esta existe para que el camino migrar -> desplegar se pruebe de extremo a extremo
desde el primer día.
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
