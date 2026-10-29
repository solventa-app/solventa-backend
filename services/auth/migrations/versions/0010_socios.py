"""socios de distribución: socios, credenciales, presupuesto y consumo (HU-W07, KAN-30)

Solo agrega (expandir, ADR-07): la revisión anterior de AUTH sigue funcionando.
- `socios`: el socio y su tasa de comisión. No hay alta por la API: se crean con la CLI
  `app.crear_socio` (W07-21).
- `credenciales_socio`: una clave por socio y entorno (PRODUCCION o PRUEBAS), **cifrada** (AES-GCM,
  con el id del socio como contexto) para que el portal la muestre. Una sola vigente por entorno;
  rotar fija `revocada_en` y crea otra. Guarda el bloqueo por intentos fallidos.
- `presupuestos_socio`: cotizaciones asignadas y consumidas (W07-9). Se reserva con un solo
  `UPDATE ... WHERE consumidas < asignadas`, así que dos cotizaciones simultáneas no pasan del tope.
- `consumos_socio`: **append-only** (trigger), una fila por llamada a la API, sin datos personales.
- `consentimientos.actor_rol` admite `SOCIO` (W07-20): el socio otorga en nombre del titular.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

ENTORNO = "entorno IN ('PRODUCCION', 'PRUEBAS')"


def upgrade() -> None:
    op.create_table(
        "socios",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("nombre", sa.Text(), nullable=False, unique=True),
        sa.Column("estado", sa.Text(), nullable=False, server_default="ACTIVO"),
        sa.Column("tasa_comision", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("estado IN ('ACTIVO', 'SUSPENDIDO')", name="ck_socios_estado"),
        sa.CheckConstraint("tasa_comision >= 0 AND tasa_comision < 1", name="ck_socios_tasa"),
    )
    op.create_table(
        "credenciales_socio",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("socio_id", sa.Text(), sa.ForeignKey("socios.id"), nullable=False),
        sa.Column("entorno", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False, unique=True),
        sa.Column("clave_cifrada", sa.Text(), nullable=False),
        sa.Column("intentos_fallidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bloqueado_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revocada_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(ENTORNO, name="ck_credenciales_socio_entorno"),
    )
    op.create_index(
        "ux_credenciales_socio_vigente",
        "credenciales_socio",
        ["socio_id", "entorno"],
        unique=True,
        postgresql_where=sa.text("revocada_en IS NULL"),
    )
    op.create_table(
        "presupuestos_socio",
        sa.Column("socio_id", sa.Text(), sa.ForeignKey("socios.id"), primary_key=True),
        sa.Column("entorno", sa.Text(), primary_key=True),
        sa.Column("api", sa.Text(), primary_key=True),
        sa.Column("asignadas", sa.Integer(), nullable=False),
        sa.Column("consumidas", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(ENTORNO, name="ck_presupuestos_socio_entorno"),
        sa.CheckConstraint("api IN ('cotizaciones')", name="ck_presupuestos_socio_api"),
        sa.CheckConstraint(
            "asignadas >= 0 AND consumidas >= 0", name="ck_presupuestos_socio_no_negativos"
        ),
    )
    op.create_table(
        "consumos_socio",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("socio_id", sa.Text(), sa.ForeignKey("socios.id"), nullable=False),
        sa.Column("entorno", sa.Text(), nullable=False),
        sa.Column("api", sa.Text(), nullable=False),
        sa.Column("resultado", sa.Text(), nullable=False),
        sa.Column("latencia_ms", sa.Integer(), nullable=False),
        sa.Column(
            "ocurrido_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(ENTORNO, name="ck_consumos_socio_entorno"),
        sa.CheckConstraint(
            "resultado IN ('OK', 'ERROR_CLIENTE', 'ERROR')", name="ck_consumos_socio_resultado"
        ),
    )
    op.create_index(
        "ix_consumos_socio_socio_ocurrido", "consumos_socio", ["socio_id", "entorno", "ocurrido_en"]
    )
    op.execute(
        """
        CREATE FUNCTION consumos_socio_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'consumos_socio es append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_consumos_socio_append_only
        BEFORE UPDATE OR DELETE ON consumos_socio
        FOR EACH ROW EXECUTE FUNCTION consumos_socio_append_only()
        """
    )
    op.drop_constraint("ck_consentimientos_actor_rol", "consentimientos", type_="check")
    op.create_check_constraint(
        "ck_consentimientos_actor_rol",
        "consentimientos",
        "actor_rol IS NULL OR actor_rol IN ('CLIENTE', 'ASESOR', 'BACKOFFICE', 'SOCIO')",
    )


def downgrade() -> None:
    # Los consentimientos que otorgó un socio no se borran (append-only): el CHECK anterior se
    # recrea `NOT VALID` para no rechazarlos, y sigue aplicando a las filas nuevas.
    op.drop_constraint("ck_consentimientos_actor_rol", "consentimientos", type_="check")
    op.execute(
        "ALTER TABLE consentimientos ADD CONSTRAINT ck_consentimientos_actor_rol "
        "CHECK (actor_rol IS NULL OR actor_rol IN ('CLIENTE', 'ASESOR', 'BACKOFFICE')) NOT VALID"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_consumos_socio_append_only ON consumos_socio")
    op.execute("DROP FUNCTION IF EXISTS consumos_socio_append_only()")
    op.drop_index("ix_consumos_socio_socio_ocurrido", table_name="consumos_socio")
    op.drop_table("consumos_socio")
    op.drop_table("presupuestos_socio")
    op.drop_index("ux_credenciales_socio_vigente", table_name="credenciales_socio")
    op.drop_table("credenciales_socio")
    op.drop_table("socios")
