"""usuarios: estados de la verificación de identidad y login por correo (HU-M01)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH sigue funcionando.
- `ck_usuarios_estado` acepta además `PENDIENTE_VERIFICACION` (cuenta creada en el onboarding,
  aún sin identidad verificada) y `RECHAZADA` (el onboarding agotó sus intentos). Ampliar el CHECK
  no rompe nada: la revisión anterior solo escribe ACTIVO y SUSPENDIDO.
- `correo_hmac`: HMAC-SHA256 del correo normalizado, para iniciar sesión con el correo sin
  guardarlo en claro (el correo sigue en `correo_cifrado`). Índice único parcial: un correo, una
  cuenta. El `titular_id` y el documento los agrega la `0008` (W03) y M01 los fija al verificar.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_usuarios_estado", "usuarios", type_="check")
    op.create_check_constraint(
        "ck_usuarios_estado",
        "usuarios",
        "estado IN ('ACTIVO', 'SUSPENDIDO', 'PENDIENTE_VERIFICACION', 'RECHAZADA')",
    )
    op.add_column("usuarios", sa.Column("correo_hmac", sa.Text(), nullable=True))
    op.create_index(
        "ux_usuarios_correo_hmac",
        "usuarios",
        ["correo_hmac"],
        unique=True,
        postgresql_where=sa.text("correo_hmac IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_usuarios_correo_hmac", table_name="usuarios")
    op.drop_column("usuarios", "correo_hmac")
    # Las cuentas de M01 que no llegaron a ACTIVO quedan SUSPENDIDAS para cumplir el CHECK anterior.
    op.execute(
        "UPDATE usuarios SET estado = 'SUSPENDIDO' "
        "WHERE estado IN ('PENDIENTE_VERIFICACION', 'RECHAZADA')"
    )
    op.drop_constraint("ck_usuarios_estado", "usuarios", type_="check")
    op.create_check_constraint(
        "ck_usuarios_estado", "usuarios", "estado IN ('ACTIVO', 'SUSPENDIDO')"
    )
