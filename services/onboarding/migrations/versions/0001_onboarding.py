"""sesiones_onboarding, datos_extraidos, verificaciones y eventos_onboarding (HU-M01)

Solo agrega (expandir, ADR-07): la base `onboarding` es nueva.

- `sesiones_onboarding`: una fila por recorrido de M01. `token_hash` es el SHA-256 del token que
  recibe la app (el token nunca se guarda). Los ids de archivo apuntan al servicio
  `almacenamiento` (propietario `onboarding`, `propietario_id` = id de la sesión).
  `titular_id` es el HMAC del número de documento (misma clave y algoritmo que W03): con él se
  ligan las pólizas previas, y su índice único parcial impide dos sesiones VERIFICADAS con el
  mismo documento (P-8).
- `estado`: INICIADO -> EN_VERIFICACION -> VERIFICADA | RECHAZADA | EN_REVISION. Una RECHAZADA con
  intentos vuelve a INICIADO con `intento + 1` (máximo 3, P-7).
- `datos_extraidos`: lo que leyó el proveedor del documento, **cifrado por campo** (AES-GCM, con la
  sesión como dato asociado). Nada en claro.
- `verificaciones`: el resultado de cada intento (códigos y puntajes, sin datos personales).
- `eventos_onboarding`: bitácora append-only **en la base** (trigger que rechaza UPDATE, DELETE y
  TRUNCATE).
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_AHORA = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "sesiones_onboarding",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("estado", sa.Text(), nullable=False, server_default="INICIADO"),
        sa.Column("intento", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("token_vence_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("jurisdiccion", sa.Text(), nullable=False),
        sa.Column("tipo_documento", sa.Text(), nullable=False),
        sa.Column("consentimiento_id", sa.Text(), nullable=True),
        sa.Column("usuario_id", sa.Text(), nullable=True),
        sa.Column("titular_id", sa.Text(), nullable=True),
        sa.Column("anverso_id", sa.Text(), nullable=True),
        sa.Column("reverso_id", sa.Text(), nullable=True),
        sa.Column("rostro_id", sa.Text(), nullable=True),
        sa.Column("video_id", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=_AHORA),
        sa.Column(
            "actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=_AHORA
        ),
        sa.CheckConstraint(
            "estado IN ('INICIADO','EN_VERIFICACION','VERIFICADA','RECHAZADA','EN_REVISION')",
            name="sesiones_estado_valido",
        ),
        sa.CheckConstraint("intento BETWEEN 1 AND 3", name="sesiones_intento_valido"),
    )
    op.create_index(
        "ux_sesiones_titular_verificado",
        "sesiones_onboarding",
        ["titular_id"],
        unique=True,
        postgresql_where=sa.text("estado = 'VERIFICADA'"),
    )
    op.create_index("ix_sesiones_usuario", "sesiones_onboarding", ["usuario_id"])

    op.create_table(
        "datos_extraidos",
        sa.Column(
            "sesion_id", sa.Text(), sa.ForeignKey("sesiones_onboarding.id"), primary_key=True
        ),
        sa.Column("nombres", sa.Text(), nullable=True),
        sa.Column("apellidos", sa.Text(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("fecha_nacimiento", sa.Text(), nullable=True),
        sa.Column("pais", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=_AHORA),
    )

    op.create_table(
        "verificaciones",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("sesion_id", sa.Text(), sa.ForeignKey("sesiones_onboarding.id"), nullable=False),
        sa.Column("intento", sa.Integer(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("puntaje_rostro", sa.Float(), nullable=True),
        sa.Column("puntaje_vida", sa.Float(), nullable=True),
        sa.Column("alertas", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=_AHORA),
    )
    op.create_index("ix_verificaciones_sesion", "verificaciones", ["sesion_id"])

    op.create_table(
        "eventos_onboarding",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("sesion_id", sa.Text(), sa.ForeignKey("sesiones_onboarding.id"), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("detalle", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=_AHORA),
    )
    op.create_index("ix_eventos_onboarding_sesion", "eventos_onboarding", ["sesion_id"])
    op.execute(
        """
        CREATE FUNCTION eventos_onboarding_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'eventos_onboarding es append-only: no se modifica ni se borra';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_eventos_onboarding_append_only
        BEFORE UPDATE OR DELETE ON eventos_onboarding
        FOR EACH ROW EXECUTE FUNCTION eventos_onboarding_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_eventos_onboarding_sin_truncate
        BEFORE TRUNCATE ON eventos_onboarding
        FOR EACH STATEMENT EXECUTE FUNCTION eventos_onboarding_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_eventos_onboarding_sin_truncate ON eventos_onboarding")
    op.execute("DROP TRIGGER IF EXISTS trg_eventos_onboarding_append_only ON eventos_onboarding")
    op.drop_table("eventos_onboarding")
    op.execute("DROP FUNCTION IF EXISTS eventos_onboarding_append_only()")
    op.drop_table("verificaciones")
    op.drop_table("datos_extraidos")
    op.drop_index("ix_sesiones_usuario", table_name="sesiones_onboarding")
    op.drop_index("ux_sesiones_titular_verificado", table_name="sesiones_onboarding")
    op.drop_table("sesiones_onboarding")
