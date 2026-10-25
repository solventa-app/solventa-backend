"""usuarios: correo y teléfono del cliente, cifrados por campo (HU-W03, bloque 8, W03-16 y W03-17)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH no conoce las columnas y sigue
funcionando. `correo_cifrado` y `telefono_cifrado` son datos personales: los cifra la app (AES-GCM,
ver `app/cifrado.py`) y la base nunca los ve en claro. Son opcionales: solo los CLIENTE los tienen
y los usuarios que ya existen quedan sin contacto. Sirven para que POLICY le envíe al cliente el
aviso de póliza emitida con el PDF adjunto.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("correo_cifrado", sa.Text(), nullable=True))
    op.add_column("usuarios", sa.Column("telefono_cifrado", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "telefono_cifrado")
    op.drop_column("usuarios", "correo_cifrado")
