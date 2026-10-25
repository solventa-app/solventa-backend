"""usuarios: idioma y moneda por defecto (KAN-209)

Solo agrega (expandir, ADR-07): las dos columnas traen valor por defecto, así que la revisión
anterior de AUTH sigue insertando usuarios sin conocerlas. No son datos personales: son
preferencias de la interfaz que el usuario elige en "Mi perfil" y la web aplica al iniciar sesión.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usuarios", sa.Column("idioma", sa.String(2), nullable=False, server_default="ES")
    )
    op.add_column(
        "usuarios", sa.Column("moneda", sa.String(3), nullable=False, server_default="COP")
    )


def downgrade() -> None:
    op.drop_column("usuarios", "moneda")
    op.drop_column("usuarios", "idioma")
