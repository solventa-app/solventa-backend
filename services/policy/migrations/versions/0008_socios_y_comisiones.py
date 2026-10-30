"""socios de distribución: tasa congelada en la propuesta y comisiones al emitir (HU-W07, KAN-30)

Solo agrega (expandir, ADR-07): la revisión anterior de POLICY sigue funcionando.
- `propuestas.tasa_comision`: la tasa del socio al aceptar (W07-7), congelada aunque cambie después
  en AUTH. Nula salvo con `actor_rol = SOCIO`, y entonces `actor_usuario_id` es el id del socio
  (W07-20), que ya tiene índice.
- `comisiones`: una por póliza emitida de un socio (`poliza_id` único, así que emitir dos veces no
  la duplica), causada **al emitir** en la misma transacción. Append-only (trigger). La prima y la
  comisión van cifradas por campo, como la prima de la propuesta (HA-SEG-002).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("propuestas", sa.Column("tasa_comision", sa.Numeric(5, 4), nullable=True))
    op.create_table(
        "comisiones",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("socio_id", sa.Text(), nullable=False),
        sa.Column("poliza_id", sa.Text(), sa.ForeignKey("polizas.id"), nullable=False, unique=True),
        sa.Column("propuesta_id", sa.Text(), sa.ForeignKey("propuestas.id"), nullable=False),
        sa.Column("tasa", sa.Numeric(5, 4), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=False),
        # {"prima": "...", "comision": "..."} cifrado, con el id de la comisión como contexto.
        sa.Column("montos_cifrados", sa.Text(), nullable=False),
        sa.Column(
            "causada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_comisiones_socio_causada", "comisiones", ["socio_id", "causada_en"])
    op.execute(
        """
        CREATE FUNCTION comisiones_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'comisiones es append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_comisiones_append_only
        BEFORE UPDATE OR DELETE ON comisiones
        FOR EACH ROW EXECUTE FUNCTION comisiones_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_comisiones_append_only ON comisiones")
    op.execute("DROP FUNCTION IF EXISTS comisiones_append_only()")
    op.drop_index("ix_comisiones_socio_causada", table_name="comisiones")
    op.drop_table("comisiones")
    op.drop_column("propuestas", "tasa_comision")
