"""cobros: cifra el token_medio_pago ya guardado (HA-SEG-002, EC022)

Desde esta versión la aplicación guarda `token_medio_pago` cifrado (AES-256-GCM, formato
`enc:v1:...`, con el id del cobro como dato asociado). Esta migración cifra las filas que
quedaron en claro. No cambia el esquema (la columna sigue siendo `text`).

- Idempotente: las filas que ya empiezan con `enc:v1:` no se tocan; correrla dos veces no cambia
  nada.
- Usa `CIFRADO_CLAVE`, la misma llave que la aplicación (en Cloud Run, de Secret Manager; allí no
  acepta la llave local ni el marcador de Terraform).
- Ventana del despliegue: la revisión anterior no entiende el prefijo `enc:v1:`. Solo afecta a un
  reintento de un cobro `pendiente` o `rechazado` que ocurra en los segundos entre la migración y
  el cambio de tráfico; en staging todos los tokens son de prueba.
- `downgrade` vuelve las filas a claro (necesita la misma llave), para poder bajar en local y CI.
"""

from alembic import op
from sqlalchemy import text

from app.application.cifrado import PREFIJO, cifrador_desde_entorno

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cifrador = cifrador_desde_entorno()
    conexion = op.get_bind()
    filas = conexion.execute(
        text("SELECT id, token_medio_pago FROM cobros WHERE token_medio_pago NOT LIKE :prefijo"),
        {"prefijo": PREFIJO + "%"},
    ).fetchall()
    for cobro_id, token in filas:
        conexion.execute(
            text("UPDATE cobros SET token_medio_pago = :token WHERE id = :id"),
            {"token": cifrador.cifrar(token, cobro_id), "id": cobro_id},
        )


def downgrade() -> None:
    cifrador = cifrador_desde_entorno()
    conexion = op.get_bind()
    filas = conexion.execute(
        text("SELECT id, token_medio_pago FROM cobros WHERE token_medio_pago LIKE :prefijo"),
        {"prefijo": PREFIJO + "%"},
    ).fetchall()
    for cobro_id, token in filas:
        conexion.execute(
            text("UPDATE cobros SET token_medio_pago = :token WHERE id = :id"),
            {"token": cifrador.descifrar(token, cobro_id), "id": cobro_id},
        )
