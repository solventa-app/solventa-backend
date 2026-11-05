"""índice para los siniestros que reportó cada cliente desde la app (HU-M05, KAN-274)

Los casos de la semilla no tienen `reportado_por`: el índice es disperso para no indexarlos.
Idempotente: `create_index` con el mismo nombre y llaves no falla.
"""


def aplicar(db) -> None:
    db["siniestros"].create_index(
        [("reportado_por", 1), ("notificado_en", -1)],
        name="ix_siniestros_reportado_por",
        unique=False,
        sparse=True,
    )
