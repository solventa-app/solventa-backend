"""propuestas: contacto por rol, código, enlace de firma, firma en tres modos y listas por rol
(HU-W03, bloque 9: W03-19 a W03-37)

Solo expandir (ADR-07): la revisión anterior no conoce las tablas ni las columnas y sigue
funcionando.

- `propuestas`:
  - `origen_contacto`: `PROPUESTA` (lo dio el ASESOR en W01-01, cifrado aquí) o `AUTH` (el CLIENTE
    tiene cuenta: POLICY lo lee de AUTH al usarlo y no lo copia), W03-25.
  - `nombre_cifrado` y `correo_cifrado`: el contacto que dio el ASESOR. Se borran si el
    consentimiento vence o se revoca antes de emitir (`contacto_borrado_en`, W03-26).
  - `actor_rol`: el rol de quien aceptó (CLIENTE o ASESOR), para el aviso al asesor y las listas.
  - `firma_texto_cifrado`: el nombre del modo «nombre estilizado» (W03-24). Va aquí y no en la
    evidencia, que es append-only, para poder borrarlo con el contacto.
- `verificaciones_identidad`: el código que el ASESOR digita (W03-20). Solo su huella (HMAC), el
  vencimiento, los intentos y si se verificó; pedir otro anula el anterior.
- `enlaces_firma`: el enlace de un solo uso de 30 min (W03-22). Solo la huella del token; uno nuevo
  anula el anterior.
- `evidencias_firma`: `modo_firma` (TRAZO, IMAGEN o NOMBRE) e `imagen_firma_archivo_id`, el id del
  archivo `firma-imagen` en ALMACENAMIENTO (W03-24 revisada: la imagen no vive en POLICY). Siguen
  append-only: la purga no borra el id, pide al almacenamiento la retención del archivo.
- El `CHECK` de estados suma `IDENTIDAD_VERIFICADA`, `EN_FIRMA`, `FIRMADA` y `CANCELADA`.
- Índices para las listas por rol (W03-35) y para la purga por consentimiento (W03-26).
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ESTADOS_ANTES = (
    "'ACEPTADA','EN_EMISION','EN_REVISION','PENDIENTE_PAGO','PAGO_RECHAZADO','NO_EMITIDA','EMITIDA'"
)
ESTADOS_AHORA = ESTADOS_ANTES + ",'IDENTIDAD_VERIFICADA','EN_FIRMA','FIRMADA','CANCELADA'"


def _creado_en() -> sa.Column:
    return sa.Column(
        "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    for columna in (
        sa.Column("origen_contacto", sa.Text(), nullable=True),
        sa.Column("nombre_cifrado", sa.Text(), nullable=True),
        sa.Column("correo_cifrado", sa.Text(), nullable=True),
        sa.Column("contacto_borrado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_rol", sa.Text(), nullable=True),
        sa.Column("firma_texto_cifrado", sa.Text(), nullable=True),
    ):
        op.add_column("propuestas", columna)
    op.create_check_constraint(
        "propuestas_origen_contacto_valido",
        "propuestas",
        "origen_contacto IS NULL OR origen_contacto IN ('PROPUESTA','AUTH')",
    )
    op.drop_constraint("propuestas_estado_valido", "propuestas", type_="check")
    op.create_check_constraint(
        "propuestas_estado_valido", "propuestas", f"estado IN ({ESTADOS_AHORA})"
    )

    op.create_table(
        "verificaciones_identidad",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("propuesta_id", sa.Text(), sa.ForeignKey("propuestas.id"), nullable=False),
        sa.Column("huella", sa.Text(), nullable=False),
        sa.Column("vence_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verificada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anulada_en", sa.DateTime(timezone=True), nullable=True),
        _creado_en(),
    )
    op.create_index(
        "ix_verificaciones_identidad_propuesta", "verificaciones_identidad", ["propuesta_id"]
    )

    op.create_table(
        "enlaces_firma",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("propuesta_id", sa.Text(), sa.ForeignKey("propuestas.id"), nullable=False),
        sa.Column("huella", sa.Text(), nullable=False, unique=True),
        sa.Column("vence_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anulado_en", sa.DateTime(timezone=True), nullable=True),
        _creado_en(),
    )
    op.create_index("ix_enlaces_firma_propuesta", "enlaces_firma", ["propuesta_id"])

    op.add_column("evidencias_firma", sa.Column("modo_firma", sa.Text(), nullable=True))
    op.add_column(
        "evidencias_firma", sa.Column("imagen_firma_archivo_id", sa.Text(), nullable=True)
    )

    op.create_index("ix_propuestas_cliente_id", "propuestas", ["cliente_id"])
    op.create_index("ix_propuestas_actor_usuario_id", "propuestas", ["actor_usuario_id"])
    op.create_index("ix_propuestas_consentimiento_id", "propuestas", ["consentimiento_id"])
    op.create_index("ix_polizas_cliente_id", "polizas", ["cliente_id"])


def downgrade() -> None:
    op.drop_index("ix_polizas_cliente_id", table_name="polizas")
    op.drop_index("ix_propuestas_consentimiento_id", table_name="propuestas")
    op.drop_index("ix_propuestas_actor_usuario_id", table_name="propuestas")
    op.drop_index("ix_propuestas_cliente_id", table_name="propuestas")
    op.drop_column("evidencias_firma", "imagen_firma_archivo_id")
    op.drop_column("evidencias_firma", "modo_firma")
    op.drop_index("ix_enlaces_firma_propuesta", table_name="enlaces_firma")
    op.drop_table("enlaces_firma")
    op.drop_index("ix_verificaciones_identidad_propuesta", table_name="verificaciones_identidad")
    op.drop_table("verificaciones_identidad")
    op.drop_constraint("propuestas_estado_valido", "propuestas", type_="check")
    op.create_check_constraint(
        "propuestas_estado_valido", "propuestas", f"estado IN ({ESTADOS_ANTES})"
    )
    op.drop_constraint("propuestas_origen_contacto_valido", "propuestas", type_="check")
    for columna in (
        "firma_texto_cifrado",
        "actor_rol",
        "contacto_borrado_en",
        "correo_cifrado",
        "nombre_cifrado",
        "origen_contacto",
    ):
        op.drop_column("propuestas", columna)
