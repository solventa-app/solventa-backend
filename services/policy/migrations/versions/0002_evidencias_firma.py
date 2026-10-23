"""evidencias_firma: la prueba de no repudio de la firma (HU-W03, KAN-211, CA-W03-03)

Solo agrega (expandir, ADR-07): tabla nueva, la revisión anterior no la toca.

- Append-only **en la base**, no solo en la app: un trigger rechaza cualquier `UPDATE` y `DELETE`, y
  otro el `TRUNCATE`. La evidencia se inserta una vez y nunca cambia: es lo que prueba qué se firmó.
- `propuesta_id` es UNIQUE: firmar dos veces la misma propuesta devuelve la misma evidencia
  (`INSERT ... ON CONFLICT (propuesta_id) DO NOTHING`).
- `documento_cifrado` guarda el documento exacto que se hasheó (prima incluida), cifrado por campo;
  con él se recalcula `documento_hash` y se prueba que es el que se firmó. El proveedor de firma
  solo ve el hash.
- `firmante_id` es el id opaco del usuario, nunca un documento ni un correo. `firmada_en` y
  `referencia_proveedor` los entrega el proveedor.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidencias_firma",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "propuesta_id", sa.Text(), sa.ForeignKey("propuestas.id"), nullable=False, unique=True
        ),
        sa.Column("documento_hash", sa.Text(), nullable=False),
        sa.Column("version_documento", sa.Text(), nullable=False),
        sa.Column("documento_cifrado", sa.Text(), nullable=False),
        sa.Column("firmante_id", sa.Text(), nullable=False),
        sa.Column("consentimiento_id", sa.Text(), nullable=True),
        sa.Column("referencia_proveedor", sa.Text(), nullable=False),
        sa.Column("firmada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        """
        CREATE FUNCTION evidencias_firma_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evidencias_firma es append-only: no se modifica ni se borra';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidencias_firma_append_only
        BEFORE UPDATE OR DELETE ON evidencias_firma
        FOR EACH ROW EXECUTE FUNCTION evidencias_firma_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidencias_firma_sin_truncate
        BEFORE TRUNCATE ON evidencias_firma
        FOR EACH STATEMENT EXECUTE FUNCTION evidencias_firma_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_evidencias_firma_sin_truncate ON evidencias_firma")
    op.execute("DROP TRIGGER IF EXISTS trg_evidencias_firma_append_only ON evidencias_firma")
    op.drop_table("evidencias_firma")
    op.execute("DROP FUNCTION IF EXISTS evidencias_firma_append_only()")
