"""consentimientos: registro append-only (T-W01-4, HA-SEG-001, CA-W01-02/08, KAN-24)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH guarda los consentimientos en
memoria y no toca esta tabla, así que puede seguir atendiendo mientras corre la migración.

- Append-only **en la base**, no solo en la app: un trigger rechaza cualquier `DELETE` y
  cualquier `UPDATE` que no sea fijar `revocado_en` por primera vez. Revocar marca la fecha;
  nunca se borra ni se reescribe un consentimiento.
- `actor_*`: quién otorgó (CA-W01-02, CA-W01-11). `actor_usuario_id` es el id opaco del
  usuario, nunca un documento ni un correo. Nulo si se registró sin sesión.
- Sin datos personales: finalidad y versión del texto son catálogos, no datos de la persona.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consentimientos",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("finalidad", sa.Text(), nullable=False),
        sa.Column("version_texto", sa.Text(), nullable=False),
        sa.Column(
            "otorgado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revocado_en", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("actor_rol", sa.Text(), nullable=True),
        sa.Column("actor_usuario_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "actor_rol IS NULL OR actor_rol IN ('CLIENTE', 'ASESOR', 'BACKOFFICE')",
            name="ck_consentimientos_actor_rol",
        ),
        sa.CheckConstraint(
            "(actor_rol IS NULL) = (actor_usuario_id IS NULL)",
            name="ck_consentimientos_actor_completo",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION consentimientos_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'consentimientos es append-only: no se borra';
            END IF;
            IF OLD.revocado_en IS NOT NULL
               OR NEW.revocado_en IS NULL
               OR (NEW.id, NEW.finalidad, NEW.version_texto, NEW.otorgado_en,
                   NEW.actor_rol, NEW.actor_usuario_id)
                  IS DISTINCT FROM
                  (OLD.id, OLD.finalidad, OLD.version_texto, OLD.otorgado_en,
                   OLD.actor_rol, OLD.actor_usuario_id)
            THEN
                RAISE EXCEPTION 'consentimientos es append-only: solo se puede revocar una vez';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_consentimientos_append_only
        BEFORE UPDATE OR DELETE ON consentimientos
        FOR EACH ROW EXECUTE FUNCTION consentimientos_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_consentimientos_append_only ON consentimientos")
    op.execute("DROP FUNCTION IF EXISTS consentimientos_append_only()")
    op.drop_table("consentimientos")
