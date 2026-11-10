"""producto de la propuesta: vida deudor o auto (HU-W08, KAN-290)

Solo agrega (expandir, ADR-07): las propuestas existentes quedan como `VIDA_DEUDOR` por el valor por
defecto, y la revisión anterior de POLICY sigue funcionando porque no lo lee. Los datos del vehículo
y las coberturas no van en columnas: viajan dentro de `oferta_cifrada`, cifrados como la prima.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "propuestas",
        sa.Column("producto", sa.Text(), nullable=False, server_default="VIDA_DEUDOR"),
    )
    op.create_check_constraint(
        "ck_propuestas_producto", "propuestas", "producto IN ('VIDA_DEUDOR', 'AUTO')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_propuestas_producto", "propuestas", type_="check")
    op.drop_column("propuestas", "producto")
