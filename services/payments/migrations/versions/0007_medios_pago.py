"""medios_pago: tarjetas tokenizadas de los clientes y su vínculo con el cobro (KAN-208)

Solo agrega (expandir, ADR-07): la revisión anterior no conoce la tabla ni la columna.

- `medios_pago` guarda el **token** del proveedor PCI-DSS, cifrado por campo (`token_cifrado`),
  más la marca y los últimos 4 dígitos para mostrar. Nunca el número de la tarjeta.
- `token_huella` (SHA-256 del token) evita registrar dos veces la misma tarjeta de un cliente
  sin tener que descifrar: `UNIQUE (cliente_id, token_huella)`.
- `cobros.medio_pago_id` dice con qué medio de pago se cobró; es nulo en los cobros hechos con
  un token suelto.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "medios_pago",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("cliente_id", sa.Text(), nullable=False),
        sa.Column("token_cifrado", sa.Text(), nullable=False),
        sa.Column("token_huella", sa.Text(), nullable=False),
        sa.Column("marca", sa.Text(), nullable=False),
        sa.Column("ultimos4", sa.Text(), nullable=False),
        sa.Column("vencimiento_mes", sa.Integer(), nullable=False),
        sa.Column("vencimiento_anio", sa.Integer(), nullable=False),
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("cliente_id", "token_huella", name="uq_medios_pago_cliente_token"),
    )
    op.create_index("idx_medios_pago_cliente", "medios_pago", ["cliente_id"])
    op.add_column(
        "cobros",
        sa.Column("medio_pago_id", sa.Text(), sa.ForeignKey("medios_pago.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cobros", "medio_pago_id")
    op.drop_index("idx_medios_pago_cliente", table_name="medios_pago")
    op.drop_table("medios_pago")
