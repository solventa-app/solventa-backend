"""polizas.aviso_omitido_motivo: por qué no se avisó al cliente de su póliza (HU-W03, W03-18)

Solo agrega (expandir, ADR-07): una columna que admite nulos; la revisión anterior no la usa.

- Nulo = el aviso sigue su curso: o ya se envió (`notificada_en`) o se reintenta.
- Con valor = el aviso **no se va a enviar** y no se reintenta: `SIN_CONTACTO` (el cliente no dejó
  ni correo ni teléfono) o `AVISO_RECHAZADO` (el ACL rechazó la solicitud, algo que reintentar no
  arregla). La póliza se emite igual y el cliente la descarga desde la web.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("polizas", sa.Column("aviso_omitido_motivo", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("polizas", "aviso_omitido_motivo")
