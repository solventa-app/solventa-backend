"""propuestas.version_reglas: con qué reglas de RATING se fijó el precio (HU-W03, CA-W03-01)

Solo agrega (expandir, ADR-07): una columna que admite nulos; la revisión anterior no la usa.

- Es la `versionReglas` de la oferta que RATING entregó al aceptarla. No es un dato personal, así
  que va en una columna y no dentro de `oferta_cifrada`: sirve para saber con qué reglas se vendió
  cada póliza cuando cambien los pesos o la fórmula.
- Nulo = la oferta no la traía (propuestas aceptadas antes de este cambio, o una oferta guardada
  en RATING antes de que la incluyera).
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("propuestas", sa.Column("version_reglas", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("propuestas", "version_reglas")
