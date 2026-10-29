"""cuenta_notificada_en en sesiones_onboarding (HU-M01, PR del contrato v0.20)

Solo agrega (expandir, ADR-07): una columna que admite null. Marca cuándo AUTH recibió el
resultado de la sesión (cuenta ACTIVO o RECHAZADA); mientras sea null, ONBOARDING lo reintenta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sesiones_onboarding",
        sa.Column("cuenta_notificada_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sesiones_onboarding", "cuenta_notificada_en")
