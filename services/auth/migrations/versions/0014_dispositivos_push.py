"""dispositivos de push y preferencia del cliente (KAN-259, HU-M04)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH sigue funcionando.

- `usuarios.notificaciones_push`: preferencia del cliente para encender o apagar el push (default
  encendido). Hoy solo tiene sentido para CLIENTE, pero vive en `usuarios` como `idioma`/`moneda`.
- `dispositivos_push`: el token FCM del teléfono. **No tiene relación con `dispositivos`** (`0013`,
  la llave para entrar con huella o rostro, KAN-26): son registros independientes del mismo
  teléfono, con reglas de "un solo activo" también independientes. El token viaja cifrado (regla
  #6), igual que el correo y el teléfono. **Un solo dispositivo con push activo por usuario**
  (`dispositivos_push_un_activo_por_usuario`, índice único parcial): al iniciar sesión en un
  dispositivo nuevo se desactivan los demás (ver `app/dispositivos_push.py` y `app/sesiones.py`).
  `familia_id` ata el dispositivo a la cadena de refresh (`refresh_tokens.familia_id`, el `sid` del
  JWT) que lo activó, para poder apagarlo si esa cadena se revoca."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usuarios",
        sa.Column("notificaciones_push", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.create_table(
        "dispositivos_push",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("usuario_id", sa.Text(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("instalacion_id", sa.Text(), nullable=False),
        sa.Column("plataforma", sa.Text(), nullable=False),
        sa.Column("familia_id", sa.Text(), nullable=False),
        sa.Column("token_fcm_cifrado", sa.Text(), nullable=True),
        sa.Column("token_fcm_hash", sa.Text(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version_app", sa.Text(), nullable=True),
        sa.Column("modelo", sa.Text(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("token_actualizado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desactivado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo_desactivacion", sa.Text(), nullable=True),
        sa.CheckConstraint("plataforma IN ('android', 'ios')", name="dispositivos_push_plataforma"),
        sa.CheckConstraint(
            "motivo_desactivacion IS NULL OR motivo_desactivacion IN "
            "('NUEVA_SESION', 'CIERRE_SESION', 'TOKEN_INVALIDO', 'CUENTA_SUSPENDIDA')",
            name="dispositivos_push_motivo",
        ),
        sa.UniqueConstraint(
            "usuario_id", "instalacion_id", name="dispositivos_push_usuario_instalacion"
        ),
    )
    op.create_index(
        "dispositivos_push_un_activo_por_usuario",
        "dispositivos_push",
        ["usuario_id"],
        unique=True,
        postgresql_where=sa.text("activo"),
    )
    op.create_index(
        "dispositivos_push_token_hash_activo",
        "dispositivos_push",
        ["token_fcm_hash"],
        postgresql_where=sa.text("activo AND token_fcm_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("dispositivos_push_token_hash_activo", table_name="dispositivos_push")
    op.drop_index("dispositivos_push_un_activo_por_usuario", table_name="dispositivos_push")
    op.drop_table("dispositivos_push")
    op.drop_column("usuarios", "notificaciones_push")
