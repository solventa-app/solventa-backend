"""usuarios: nombre del cliente, cifrado por campo (KAN-28)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH no conoce la columna y sigue
funcionando. `nombre_cifrado` es dato personal: lo cifra la app (AES-GCM, ver `app/cifrado.py`)
y la base nunca lo ve en claro. Es opcional: solo los CLIENTE tienen nombre.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("nombre_cifrado", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "nombre_cifrado")
