"""programacion_cobros: el cobro recurrente de cada póliza (HU-W05, W05-A7)

Una fila por propuesta, creada al emitir la póliza con el medio del primer cobro.

- `propuesta_id` es único: pedir la programación dos veces no la duplica.
- `intervalo_dias` se copia al crear, para que cambiar `COBRO_RECURRENTE_INTERVALO_DIAS` no mueva
  las pólizas que ya existen.
- `ciclo` numera los cobros generados: con la propuesta da su `id_intento_cobro`.
- `vigencia_hasta`: al superarla, la programación queda inactiva y no se cobra más.
- `propuesta_id` y `poliza_id` son referencias a POLICY: sin llave foránea, como `cobros.poliza_id`.

Solo agrega (expandir, ADR-07): la revisión anterior no conoce la tabla y sigue funcionando.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "programacion_cobros",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("propuesta_id", sa.Text(), nullable=False, unique=True),
        sa.Column("poliza_id", sa.Text(), nullable=False),
        sa.Column("cliente_id", sa.Text(), nullable=True),
        sa.Column("medio_pago_id", sa.Text(), sa.ForeignKey("medios_pago.id"), nullable=False),
        sa.Column("monto", sa.Text(), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=False),
        sa.Column("intervalo_dias", sa.Integer(), nullable=False),
        sa.Column("ciclo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proximo_cobro_en", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("vigencia_hasta", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "intervalo_dias BETWEEN 1 AND 31", name="programacion_cobros_intervalo_valido"
        ),
    )
    op.create_index(
        "idx_programacion_cobros_vencidas",
        "programacion_cobros",
        ["proximo_cobro_en"],
        postgresql_where=sa.text("activa"),
    )


def downgrade() -> None:
    op.drop_index("idx_programacion_cobros_vencidas", table_name="programacion_cobros")
    op.drop_table("programacion_cobros")
