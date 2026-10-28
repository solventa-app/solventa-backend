"""indemnizaciones: orden de pago de la indemnización de un siniestro aprobado (HU-W04, W04-6)

CLAIMS la pide al aprobar un siniestro, antes de guardar la aprobación. Es solo la orden: queda
`PENDIENTE_DISPERSION` y la dispersión por la pasarela sigue en el Sprint 3 (CA-W05-07).

- `siniestro_id` es único: pedir la orden dos veces para el mismo siniestro devuelve la misma
  (idempotencia; CLAIMS puede reintentar sin duplicar el pago).
- `siniestro_id` y `poliza_id` son referencias a otros servicios (CLAIMS y POLICY): sin llave
  foránea, como `cobros.poliza_id`.
- El monto va como texto decimal con su moneda, como en `cobros`.

Solo agrega (expandir, ADR-07): la revisión anterior no conoce la tabla y sigue funcionando.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "indemnizaciones",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("siniestro_id", sa.Text(), nullable=False, unique=True),
        sa.Column("poliza_id", sa.Text(), nullable=False),
        sa.Column("monto", sa.Text(), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=False),
        sa.Column(
            "estado", sa.Text(), nullable=False, server_default=sa.text("'PENDIENTE_DISPERSION'")
        ),
        sa.Column("metodo_transferencia", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE_DISPERSION')", name="indemnizaciones_estado_valido"
        ),
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
    )
    op.create_index("idx_indemnizaciones_poliza", "indemnizaciones", ["poliza_id"])


def downgrade() -> None:
    op.drop_index("idx_indemnizaciones_poliza", table_name="indemnizaciones")
    op.drop_table("indemnizaciones")
