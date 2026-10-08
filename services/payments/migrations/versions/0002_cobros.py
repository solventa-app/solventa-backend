"""cobros: estados de cobro, idempotencia y balances (T-W05-3, KAN-28)

Traducción exacta del esquema ya construido y verificado en
`app/application/repositorio_cobros.py` (antes en `app/sql/001_cobros.sql`, ahora borrado: el
esquema lo crea esta migración, no la app al arrancar — ADR-07). `clave_idempotencia` es la
columna que hace la idempotencia correcta bajo concurrencia real (`UNIQUE` + `ON CONFLICT ...
DO NOTHING RETURNING`, ver services/payments/README.md).
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cobros",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("clave_idempotencia", sa.Text(), nullable=False, unique=True),
        sa.Column("poliza_id", sa.Text(), nullable=False),
        sa.Column("monto", sa.Text(), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=False),
        sa.Column("token_medio_pago", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False),
        sa.Column("referencia", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="1"),
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
    op.create_index("idx_cobros_poliza", "cobros", ["poliza_id"])
    op.create_index("idx_cobros_estado", "cobros", ["estado"])


def downgrade() -> None:
    op.drop_index("idx_cobros_estado", table_name="cobros")
    op.drop_index("idx_cobros_poliza", table_name="cobros")
    op.drop_table("cobros")
