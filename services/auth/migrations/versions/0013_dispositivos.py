"""dispositivos de confianza para entrar con huella o rostro (KAN-26)

Solo agrega (expandir, ADR-07). Cada fila es la **llave pública** (EC P-256, SPKI en base64) de un
teléfono; la privada nunca sale del Keystore/Keychain del dispositivo y solo se usa tras la
biometría. Con ella la app firma un reto de AUTH y entra sin escribir la clave, aunque la sesión
anterior se haya cerrado. Una cuenta tiene un solo dispositivo activo: registrar otro revoca el
anterior. Cambiar la clave los revoca todos.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dispositivos",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("usuario_id", sa.Text(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("clave_publica", sa.Text(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ultimo_uso", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("dispositivos_usuario", "dispositivos", ["usuario_id"])


def downgrade() -> None:
    op.drop_index("dispositivos_usuario", table_name="dispositivos")
    op.drop_table("dispositivos")
