"""sesiones de pago, medios de pago y origen del cobro (HU-W03, W03-28 a W03-30; W05-A1 a A3)

El titular paga en la página alojada de la pasarela (W03-28): Solventa abre una sesión, el navegador
paga allí y PAYMENTS confirma el resultado de servidor a servidor.

- `sesiones_pago`: una fila por sesión abierta (o intentada) para una propuesta. Cada sesión es un
  intento nuevo ante la pasarela, con `id_intento_cobro = uuid5(propuesta, n)` (único): una tarjeta
  nueva o una sesión vencida nunca reutilizan la referencia de otra. `fallos_seguidos` es el
  contador de fallas de la pasarela de la propuesta en esa sesión (W03-30: al tercero, otra
  tarjeta).
  `proveedor_sesion_id` es el id de la pasarela, el que vuelve en la URL de regreso.
- `medios_pago`: el medio tokenizado que dejó un pago aprobado. Solo el token **cifrado** (con el id
  del medio como dato asociado), la marca y el tipo; **ni el número ni los últimos dígitos**
  (W05-A2). Queda listo para el cobro recurrente (W05-A7, PAYMENTS `0009`).
- `cobros.origen`: `INTERACTIVO` (lo pagó el titular en la página) o `RECURRENTE`. Las filas que ya
  existen y los cobros de `POST /cobros` son `RECURRENTE` (cobro con token). `/cobros/_reintentar`
  no toca los interactivos (W05-A3).
- `cobros.medio_pago_id`: de qué medio salió el cobro (W05-A5: marca y tipo en la tabla).

Solo agrega (expandir, ADR-07): la revisión anterior no conoce las tablas ni las columnas y sigue
funcionando; `origen` tiene valor por defecto.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _fechas() -> list[sa.Column]:
    return [
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "actualizado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "medios_pago",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("propuesta_id", sa.Text(), nullable=False),
        sa.Column("cliente_id", sa.Text(), nullable=True),
        sa.Column("token_cifrado", sa.Text(), nullable=False),
        sa.Column("marca", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.CheckConstraint("tipo IN ('CREDITO','DEBITO')", name="medios_pago_tipo_valido"),
        *_fechas(),
    )
    op.create_index("idx_medios_pago_propuesta", "medios_pago", ["propuesta_id"])

    op.create_table(
        "sesiones_pago",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("propuesta_id", sa.Text(), nullable=False),
        sa.Column("cliente_id", sa.Text(), nullable=True),
        sa.Column("intento", sa.Integer(), nullable=False),
        sa.Column("id_intento_cobro", sa.Text(), nullable=False, unique=True),
        sa.Column("proveedor_sesion_id", sa.Text(), nullable=True, unique=True),
        sa.Column("estado", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("monto", sa.Text(), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=False),
        sa.Column("url_pago", sa.Text(), nullable=True),
        sa.Column("vence_en", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("fallos_seguidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cobro_id", sa.Text(), sa.ForeignKey("cobros.id"), nullable=True),
        sa.CheckConstraint(
            "estado IN ('abierta','cobrada','rechazada','fallida','vencida','no_disponible')",
            name="sesiones_pago_estado_valido",
        ),
        sa.UniqueConstraint("propuesta_id", "intento", name="sesiones_pago_propuesta_intento"),
        *_fechas(),
    )

    op.add_column(
        "cobros",
        sa.Column("origen", sa.Text(), nullable=False, server_default="RECURRENTE"),
    )
    op.add_column(
        "cobros",
        sa.Column("medio_pago_id", sa.Text(), sa.ForeignKey("medios_pago.id"), nullable=True),
    )
    op.create_check_constraint(
        "cobros_origen_valido", "cobros", "origen IN ('INTERACTIVO','RECURRENTE')"
    )


def downgrade() -> None:
    op.drop_constraint("cobros_origen_valido", "cobros", type_="check")
    op.drop_column("cobros", "medio_pago_id")
    op.drop_column("cobros", "origen")
    op.drop_table("sesiones_pago")
    op.drop_index("idx_medios_pago_propuesta", table_name="medios_pago")
    op.drop_table("medios_pago")
