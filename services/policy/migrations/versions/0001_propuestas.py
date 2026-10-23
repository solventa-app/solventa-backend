"""propuestas: la oferta COMPLETA aceptada por el cliente (HU-W03, KAN-209, CA-W03-01)

Solo agrega (expandir, ADR-07): la base `policy` es nueva, no hay una revisión anterior que atender.

- `oferta_id` es UNIQUE: aceptar dos veces la misma oferta devuelve la misma propuesta (idempotencia
  en la base, no solo en la app; `INSERT ... ON CONFLICT (oferta_id) DO NOTHING`).
- `oferta_cifrada`: prima, cobertura y plazo que POLICY leyó de RATING, cifrados por campo
  (AES-GCM, el id de la propuesta como dato asociado). El precio nunca lo manda el cliente.
- `cliente_id` es el identificador opaco (HMAC) del titular; nunca un documento.
- `actor_usuario_id`: el usuario (CLIENTE o ASESOR) que aceptó, para el rastro de quién hizo qué.
- Las tablas de póliza y de evidencia de firma llegan con sus bloques (4 y 2), cada una en su
  propia migración.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "propuestas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("oferta_id", sa.Text(), nullable=False),
        sa.Column("cliente_id", sa.Text(), nullable=False),
        sa.Column("consentimiento_id", sa.Text(), nullable=True),
        sa.Column("actor_usuario_id", sa.Text(), nullable=True),
        sa.Column("estado", sa.Text(), nullable=False, server_default="ACEPTADA"),
        sa.Column("oferta_cifrada", sa.Text(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "estado IN ('ACEPTADA','EN_EMISION','EN_REVISION','PENDIENTE_PAGO',"
            "'PAGO_RECHAZADO','NO_EMITIDA','EMITIDA')",
            name="propuestas_estado_valido",
        ),
    )
    op.create_index("ix_propuestas_oferta_id", "propuestas", ["oferta_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_propuestas_oferta_id", table_name="propuestas")
    op.drop_table("propuestas")
