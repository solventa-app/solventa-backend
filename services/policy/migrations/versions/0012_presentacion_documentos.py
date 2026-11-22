"""presentacion_documentos: el idioma y la moneda con que se firmó (KAN-26)

Solo expandir (ADR-07): columna nueva y opcional en `evidencias_firma`. La revisión anterior no la
lee y sigue funcionando; una firma sin presentación genera sus documentos en español y en COP, como
antes.

Guarda el idioma (`es` o `en`), la moneda elegida, la tasa de referencia (COP por unidad) y la
fecha de esa tasa que tenía la web del titular al firmar. Con ellos se generan el contrato firmado
y la póliza (también la que se emite después por conciliación y la que viaja en el correo), así
que el documento queda en el idioma y la moneda de ese momento aunque el usuario los cambie después.
No es un dato personal.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evidencias_firma",
        sa.Column("presentacion", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evidencias_firma", "presentacion")
