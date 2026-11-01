"""refresh tokens de sesión (T22, KAN-26)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH sigue funcionando.
- `refresh_tokens`: el refresh es opaco y aquí solo vive su **hash** (SHA-256). Rota en cada uso:
  el anterior queda con `usado_en`; todos los de una cadena comparten `familia_id`, y reusar uno
  ya usado revoca la cadena entera. Cada uno queda atado al canal con que se emitió.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("familia_id", sa.Text(), nullable=False),
        sa.Column("usuario_id", sa.Text(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("hash_token", sa.Text(), nullable=False, unique=True),
        sa.Column("canal", sa.Text(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("canal IN ('app', 'web')", name="refresh_tokens_canal"),
    )
    op.create_index("refresh_tokens_familia", "refresh_tokens", ["familia_id"])
    op.create_index("refresh_tokens_usuario", "refresh_tokens", ["usuario_id"])


def downgrade() -> None:
    op.drop_index("refresh_tokens_usuario", table_name="refresh_tokens")
    op.drop_index("refresh_tokens_familia", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
