"""polizas: la póliza emitida y el seguimiento de la emisión (HU-W03, KAN-212 y KAN-213)

Solo agrega (expandir, ADR-07): una tabla, una secuencia y dos columnas con valor por defecto; la
revisión anterior no las usa.

- `polizas.propuesta_id` es UNIQUE: emitir dos veces la misma propuesta devuelve la misma póliza
  (`INSERT ... ON CONFLICT (propuesta_id) DO NOTHING`), así que reintentar la emisión no duplica.
- `numero` sale de una secuencia de Postgres (`polizas_numero_seq`): único y creciente aunque dos
  emisiones corran a la vez. Los huecos (un rollback consume un número) son normales y no importan.
- La póliza no guarda dinero: la prima y la cobertura viven, cifradas, en la propuesta. Aquí solo
  están las fechas de vigencia y el cliente opaco.
- `propuestas.intentos_emision` acota los reintentos de un primer cobro pendiente; sin tope, una
  propuesta que nunca se cobra se reprogramaría para siempre.
- `propuestas.motivo_estado` guarda los códigos (sin datos personales ni montos) de por qué una
  propuesta quedó en revisión o no se emitió.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE polizas_numero_seq")
    op.create_table(
        "polizas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "propuesta_id", sa.Text(), sa.ForeignKey("propuestas.id"), nullable=False, unique=True
        ),
        sa.Column("numero", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("cliente_id", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False, server_default="EMITIDA"),
        sa.Column("vigencia_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigencia_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("estado IN ('EMITIDA')", name="polizas_estado_valido"),
    )
    op.add_column(
        "propuestas",
        sa.Column("intentos_emision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("propuestas", sa.Column("motivo_estado", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("propuestas", "motivo_estado")
    op.drop_column("propuestas", "intentos_emision")
    op.drop_table("polizas")
    op.execute("DROP SEQUENCE IF EXISTS polizas_numero_seq")
