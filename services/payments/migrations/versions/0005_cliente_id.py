"""cobros: cliente_id, a quién se le cobra (KAN-28)

Solo agrega (expandir, ADR-07): la revisión anterior no conoce la columna. Es opcional porque
los cobros anteriores no lo tienen, y es solo el id del usuario CLIENTE en AUTH: el nombre
vive allá, cifrado (escritor único), nunca en esta tabla.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cobros", sa.Column("cliente_id", sa.Text(), nullable=True))
    op.create_index("idx_cobros_cliente", "cobros", ["cliente_id"])


def downgrade() -> None:
    op.drop_index("idx_cobros_cliente", table_name="cobros")
    op.drop_column("cobros", "cliente_id")
