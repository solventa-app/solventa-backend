"""documentos_firmados: el contrato firmado y la póliza guardados, cifrados (KAN-26, T6, D-1)

Solo expandir (ADR-07): tabla nueva; la revisión anterior no la conoce y sigue funcionando (regenera
los PDF al pedirlos, como antes).

Una fila por propuesta y tipo (`contrato-firmado` o `poliza-pdf`) con el id del archivo en
ALMACENAMIENTO, la llave de datos **envuelta** (Cloud KMS, o la llave de desarrollo en local), la
huella SHA-256 del PDF en claro y su tamaño. El PDF cifrado vive en el bucket; aquí no hay ni el PDF
ni la llave de datos en claro. Inmutables: no se actualizan.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documentos_firmados",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("propuesta_id", sa.Text(), sa.ForeignKey("propuestas.id"), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("archivo_id", sa.Text(), nullable=False),
        sa.Column("clave_envuelta", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("tamano_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("propuesta_id", "tipo", name="documentos_firmados_propuesta_tipo"),
        sa.CheckConstraint(
            "tipo IN ('contrato-firmado','poliza-pdf')", name="documentos_firmados_tipo_valido"
        ),
    )


def downgrade() -> None:
    op.drop_table("documentos_firmados")
