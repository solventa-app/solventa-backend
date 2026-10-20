"""consentimientos: titular opaco, metadatos de autorización y vencimiento de 30 días
(KAN-24, decisiones 20 y 23, CA-W01-01/02/08, HA-SEG-001).

Solo agrega (expandir, ADR-07):
- `titular_id`: hash HMAC-SHA256 opaco del documento de identidad (nunca el documento en claro).
- `tipo_dato`, `fuente_datos`, `canal`: alcance autorizado por el titular.
- `vence_en`: fecha de vencimiento (`otorgado_en + 30 días`); las filas existentes se rellenan
  igual y queda `NOT NULL DEFAULT (now() + interval '30 days')`.
- El trigger append-only (`consentimientos_append_only`) se actualiza para impedir también
  cualquier cambio en `vence_en`, `titular_id`, `tipo_dato`, `fuente_datos` o `canal` en un
  `UPDATE` (solo permite fijar `revocado_en` cuando era `NULL`).
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("consentimientos", sa.Column("titular_id", sa.Text(), nullable=True))
    op.add_column("consentimientos", sa.Column("tipo_dato", sa.Text(), nullable=True))
    op.add_column("consentimientos", sa.Column("fuente_datos", sa.Text(), nullable=True))
    op.add_column("consentimientos", sa.Column("canal", sa.Text(), nullable=True))
    op.add_column(
        "consentimientos", sa.Column("vence_en", sa.TIMESTAMP(timezone=True), nullable=True)
    )

    op.execute("ALTER TABLE consentimientos DISABLE TRIGGER trg_consentimientos_append_only")
    op.execute(
        """
        UPDATE consentimientos
        SET vence_en = otorgado_en + interval '30 days'
        WHERE vence_en IS NULL
        """
    )
    op.execute("ALTER TABLE consentimientos ENABLE TRIGGER trg_consentimientos_append_only")

    op.execute("ALTER TABLE consentimientos ALTER COLUMN vence_en SET NOT NULL")
    op.execute(
        "ALTER TABLE consentimientos ALTER COLUMN vence_en SET DEFAULT (now() + interval '30 days')"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION consentimientos_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'consentimientos es append-only: no se borra';
            END IF;
            IF OLD.revocado_en IS NOT NULL
               OR NEW.revocado_en IS NULL
               OR (NEW.id, NEW.finalidad, NEW.version_texto, NEW.otorgado_en,
                   NEW.actor_rol, NEW.actor_usuario_id,
                   NEW.titular_id, NEW.tipo_dato, NEW.fuente_datos, NEW.canal, NEW.vence_en)
                  IS DISTINCT FROM
                  (OLD.id, OLD.finalidad, OLD.version_texto, OLD.otorgado_en,
                   OLD.actor_rol, OLD.actor_usuario_id,
                   OLD.titular_id, OLD.tipo_dato, OLD.fuente_datos, OLD.canal, OLD.vence_en)
            THEN
                RAISE EXCEPTION 'consentimientos es append-only: solo se puede revocar una vez';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION consentimientos_append_only() RETURNS trigger AS $$
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
    op.drop_column("consentimientos", "vence_en")
    op.drop_column("consentimientos", "canal")
    op.drop_column("consentimientos", "fuente_datos")
    op.drop_column("consentimientos", "tipo_dato")
    op.drop_column("consentimientos", "titular_id")
