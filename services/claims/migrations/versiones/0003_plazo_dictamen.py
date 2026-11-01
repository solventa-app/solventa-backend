"""índice para hallar los siniestros con el plazo del dictamen vencido (D-3, revisión QA)

Idempotente: `create_index` con el mismo nombre y llaves no falla.
"""


def aplicar(db) -> None:
    db["siniestros"].create_index(
        [("estado", 1), ("vence_dictamen_en", 1)], name="ix_estado_vence_dictamen", unique=False
    )
