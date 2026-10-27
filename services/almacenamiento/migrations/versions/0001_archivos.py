"""archivos y accesos_archivo: metadatos de los archivos que viven en Cloud Storage (KAN-250)

Solo agrega (expandir, ADR-07): la base `almacenamiento` es nueva.

- `archivos`: una fila por archivo. Los bytes NO están aquí, sino en el bucket (`objeto` es su
  nombre, UNIQUE). Sin nombres originales ni datos personales: `propietario_id`, `subido_por_id` y
  `consentimiento_id` son identificadores opacos (regla 6): no hay nada que cifrar por campo.
  El contenido se cifra en el bucket con CMEK (infra/almacenamiento.tf).
- `estado`: PENDIENTE (URL emitida, aún sin validar) -> DISPONIBLE | RECHAZADO; DISPONIBLE ->
  ELIMINADO cuando vence la retención. Un archivo nunca cambia de contenido (inmutable).
- `derivado_de`: linaje (p. ej. un fotograma sacado de un video).
- `retener_desde`: desde cuándo corre la retención del tipo (`customTime` del objeto).
- `accesos_archivo`: cada URL firmada que se emitió, a quién y hasta cuándo. Append-only **en la
  base**: un trigger rechaza `UPDATE`, `DELETE` y `TRUNCATE`.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "archivos",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False, server_default="PENDIENTE"),
        sa.Column("propietario_tipo", sa.Text(), nullable=False),
        sa.Column("propietario_id", sa.Text(), nullable=False),
        sa.Column("subido_por", sa.Text(), nullable=False),
        sa.Column("subido_por_id", sa.Text(), nullable=True),
        sa.Column("consentimiento_id", sa.Text(), nullable=True),
        sa.Column("tipo_contenido", sa.Text(), nullable=False),
        sa.Column("tamano_declarado", sa.BigInteger(), nullable=False),
        sa.Column("tamano_bytes", sa.BigInteger(), nullable=True),
        sa.Column("crc32c", sa.Text(), nullable=True),
        sa.Column("objeto", sa.Text(), nullable=False),
        sa.Column("derivado_de", sa.Text(), sa.ForeignKey("archivos.id"), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("retener_desde", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("disponible_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eliminado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE','DISPONIBLE','RECHAZADO','ELIMINADO')",
            name="archivos_estado_valido",
        ),
        sa.CheckConstraint("tamano_declarado > 0", name="archivos_tamano_positivo"),
    )
    op.create_index("ix_archivos_objeto", "archivos", ["objeto"], unique=True)
    op.create_index("ix_archivos_propietario", "archivos", ["propietario_tipo", "propietario_id"])
    # Para el barrido de retención: solo las filas que pueden vencer.
    op.create_index(
        "ix_archivos_retencion",
        "archivos",
        ["retener_desde"],
        postgresql_where=sa.text("estado = 'DISPONIBLE' AND retener_desde IS NOT NULL"),
    )

    op.create_table(
        "accesos_archivo",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("archivo_id", sa.Text(), sa.ForeignKey("archivos.id"), nullable=False),
        sa.Column("accion", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column(
            "emitido_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("vence_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("accion IN ('SUBIDA','DESCARGA')", name="accesos_accion_valida"),
    )
    op.create_index("ix_accesos_archivo_archivo", "accesos_archivo", ["archivo_id"])
    op.execute(
        """
        CREATE FUNCTION accesos_archivo_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'accesos_archivo es append-only: no se modifica ni se borra';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accesos_archivo_append_only
        BEFORE UPDATE OR DELETE ON accesos_archivo
        FOR EACH ROW EXECUTE FUNCTION accesos_archivo_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accesos_archivo_sin_truncate
        BEFORE TRUNCATE ON accesos_archivo
        FOR EACH STATEMENT EXECUTE FUNCTION accesos_archivo_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_accesos_archivo_sin_truncate ON accesos_archivo")
    op.execute("DROP TRIGGER IF EXISTS trg_accesos_archivo_append_only ON accesos_archivo")
    op.drop_table("accesos_archivo")
    op.execute("DROP FUNCTION IF EXISTS accesos_archivo_append_only()")
    op.drop_index("ix_archivos_retencion", table_name="archivos")
    op.drop_index("ix_archivos_propietario", table_name="archivos")
    op.drop_index("ix_archivos_objeto", table_name="archivos")
    op.drop_table("archivos")
