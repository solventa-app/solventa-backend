"""usuarios: alias, hash de la clave, rol, estado y bloqueo por intentos (HU-M02, KAN-24)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH no usa esta tabla, así que
puede seguir atendiendo mientras corre la migración.

- `usuario` es el alias de acceso, único. No es un correo ni un documento (lo valida la
  app), así que la tabla no guarda datos personales que haya que cifrar (SEG-002).
- `hash_clave` es argon2id; la clave nunca se guarda.
- Baja lógica con `estado`: nunca se borran filas.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("usuario", sa.Text(), nullable=False, unique=True),
        sa.Column("hash_clave", sa.Text(), nullable=False),
        sa.Column("rol", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False, server_default="ACTIVO"),
        sa.Column("intentos_fallidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bloqueado_hasta", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "actualizado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("rol IN ('CLIENTE', 'ASESOR', 'BACKOFFICE')", name="ck_usuarios_rol"),
        sa.CheckConstraint("estado IN ('ACTIVO', 'SUSPENDIDO')", name="ck_usuarios_estado"),
    )
    op.create_index("idx_usuarios_rol", "usuarios", ["rol"])


def downgrade() -> None:
    op.drop_index("idx_usuarios_rol", table_name="usuarios")
    op.drop_table("usuarios")
