"""${message}

Revisa: ${down_revision | comma,n}
Creada: ${create_date}

Recuerda: cambios compatibles hacia atrás (expandir ahora, contraer en un despliegue posterior) y
`downgrade` que deshaga `upgrade`. Ver docs/adr/ADR-07-cicd-y-migraciones.md.
"""

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
