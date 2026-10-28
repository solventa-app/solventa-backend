"""usuarios: titular verificado y documento cifrado del CLIENTE (HU-W03, W03-27, W03-34 y W03-36)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH no conoce las columnas y sigue
funcionando.

- `titular_id` es el HMAC del documento, el mismo que guarda el consentimiento y que POLICY usa como
  `cliente_id` de propuestas y pólizas. **Solo se fija con la identidad verificada**: el BACKOFFICE
  al crear el CLIENTE (W03-27) o la verificación de M01 después (W03-36). Con él, «Mis pólizas»
  liga las pólizas que un asesor le vendió antes de que tuviera cuenta. Único: una persona, una
  cuenta de CLIENTE.
- `documento_cifrado` es dato personal: lo cifra la app (AES-GCM, `app/cifrado.py`) y la base nunca
  lo ve en claro. Sirve para que W01 no le pida la cédula al CLIENTE (el BFF la toma de AUTH) y para
  mostrarla enmascarada.

Los usuarios que ya existen quedan sin titular ni documento; los de prueba se recrean.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("titular_id", sa.Text(), nullable=True))
    op.add_column("usuarios", sa.Column("documento_cifrado", sa.Text(), nullable=True))
    op.create_index(
        "usuarios_titular_unico",
        "usuarios",
        ["titular_id"],
        unique=True,
        postgresql_where=sa.text("titular_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("usuarios_titular_unico", table_name="usuarios")
    op.drop_column("usuarios", "documento_cifrado")
    op.drop_column("usuarios", "titular_id")
