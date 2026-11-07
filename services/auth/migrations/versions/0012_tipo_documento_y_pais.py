"""usuarios: tipo de documento y país de la cuenta (T20, KAN-26)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH no conoce las columnas y sigue
funcionando.

- `tipo_documento`: el tipo del documento del CLIENTE (CC, CE, CURP, CPF, RUT, DNI, SSN, DNI_NIE o
  PASAPORTE, de `jurisdicciones.json`). No es dato personal por sí solo; el número sigue cifrado en
  `documento_cifrado`.
- `pais`: el país de la cuenta (uno de los siete). Lo trae el CLIENTE de su documento y cualquier
  usuario lo puede cambiar en sus preferencias (`actualizarPreferencias`).

Las cuentas que ya existen quedan sin tipo ni país: se leen como cédula colombiana (`CO`/`CC`),
que es lo único que se podía registrar antes, y su `titular_id` no cambia (`valor_titular`).
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("tipo_documento", sa.Text(), nullable=True))
    op.add_column("usuarios", sa.Column("pais", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "pais")
    op.drop_column("usuarios", "tipo_documento")
