"""polizas.notificada_en: cuándo se avisó al cliente de su póliza (HU-W03, KAN-215, CA-W03-08)

Solo agrega (expandir, ADR-07): una columna que admite nulos; la revisión anterior no la usa.

- `notificada_en` nulo = todavía no se avisó. Mientras sea nulo, repetir la emisión reintenta el
  aviso (el proveedor es idempotente por `poliza-emitida-{id}`, así que no manda dos). Con valor, no
  se vuelve a avisar: la entrega del documento es una obligación con el asegurado y se cumple una
  vez.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("polizas", sa.Column("notificada_en", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("polizas", "notificada_en")
