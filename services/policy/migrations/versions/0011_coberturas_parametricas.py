"""coberturas_parametricas: el producto paramétrico (VUELO/CLIMA) de una póliza (KAN-259, HU-M04,
T-M04-5, Fase 4, D8 del plan de notificaciones push)

Solo expandir (ADR-07): tabla nueva; la revisión anterior no la conoce y sigue funcionando.

POLICY sigue siendo el escritor único de sus tablas (regla #1); CLAIMS **nunca** escribe aquí, solo
lee por `GET /interno/coberturas-parametricas` para cruzar un evento (clima o vuelo) con las
pólizas vigentes y decidir si paga el monto fijo.

- `tipo = VUELO`: `numero_vuelo`, `fecha_salida`, `aeropuerto_origen` y `umbral_retraso_min`
  (minutos; también paga si el vuelo se cancela, sin importar el retraso). Índice
  `(tipo, numero_vuelo, fecha_salida)` para el cruce exacto que usa el ACL/CLAIMS
  (`evento.parametrico.recibido.datos.clave = "<numeroVuelo>:<fecha>"`).
- `tipo = CLIMA`: `latitud`, `longitud`, `variable` y `umbral` (en la unidad propia de la
  variable), más `ventana_horas` (la ventana de acumulación de la regla, informativa en esta
  fase: el cruce exacto de la ventana lo hace CLAIMS con `medidoEn`). Sin un helper de celda geo
  en POLICY todavía: la "celda" es lat/lon redondeados a 2 decimales (~1.1 km en el ecuador, más
  fino que los ~10 km del plan), una limitación simple y documentada aquí; cuando exista un
  geohash real, esta tabla se amplía sin romper nada (ADR-07). El índice es una expresión sobre
  `round(latitud, 2)`/`round(longitud, 2)` para que el cruce por celda use el índice.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coberturas_parametricas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("poliza_id", sa.Text(), sa.ForeignKey("polizas.id"), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("monto_fijo", sa.Numeric(14, 2), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=False),
        sa.Column("vigencia_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vigencia_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.Text(), nullable=False, server_default="VIGENTE"),
        # VUELO
        sa.Column("numero_vuelo", sa.Text(), nullable=True),
        sa.Column("fecha_salida", sa.Date(), nullable=True),
        sa.Column("aeropuerto_origen", sa.Text(), nullable=True),
        sa.Column("umbral_retraso_min", sa.Integer(), nullable=True),
        # CLIMA
        sa.Column("latitud", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitud", sa.Numeric(9, 6), nullable=True),
        sa.Column("variable", sa.Text(), nullable=True),
        sa.Column("umbral", sa.Numeric(14, 4), nullable=True),
        sa.Column("ventana_horas", sa.Integer(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("tipo IN ('VUELO','CLIMA')", name="coberturas_parametricas_tipo_valido"),
        sa.CheckConstraint(
            "estado IN ('VIGENTE','INACTIVA')", name="coberturas_parametricas_estado_valido"
        ),
        sa.CheckConstraint(
            "(tipo <> 'VUELO') OR "
            "(numero_vuelo IS NOT NULL AND fecha_salida IS NOT NULL "
            "AND umbral_retraso_min IS NOT NULL)",
            name="coberturas_parametricas_vuelo_completo",
        ),
        sa.CheckConstraint(
            "(tipo <> 'CLIMA') OR "
            "(latitud IS NOT NULL AND longitud IS NOT NULL AND variable IS NOT NULL "
            "AND umbral IS NOT NULL)",
            name="coberturas_parametricas_clima_completo",
        ),
        sa.CheckConstraint(
            "variable IS NULL OR variable IN ('PRECIPITACION_MM','VIENTO_KMH','TEMP_MAX_C')",
            name="coberturas_parametricas_variable_valida",
        ),
    )
    op.create_index(
        "ix_coberturas_parametricas_vuelo",
        "coberturas_parametricas",
        ["tipo", "numero_vuelo", "fecha_salida"],
    )
    op.execute(
        "CREATE INDEX ix_coberturas_parametricas_celda ON coberturas_parametricas "
        "(tipo, round(latitud, 2), round(longitud, 2))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_coberturas_parametricas_celda")
    op.drop_index("ix_coberturas_parametricas_vuelo", table_name="coberturas_parametricas")
    op.drop_table("coberturas_parametricas")
