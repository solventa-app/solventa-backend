"""índice para «Mis asistencias» (HU-M06, contrato v0.52, B6)

`misAsistencias` lee las del cliente de los últimos 30 días, de la más reciente a la más vieja.
`confirmada_en` y `llegada_estimada` llegan sin cambiar el validador (no es estricto con campos
extra) y `LLEGO` no se guarda: se deriva al leer.

Idempotente: `create_index` con el mismo nombre y llaves no falla.
"""


def aplicar(db) -> None:
    db["asistencias"].create_index(
        [("cliente_id", 1), ("solicitada_en", -1)], name="ix_cliente_solicitada", unique=False
    )
