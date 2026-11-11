"""consultas: historial de las consultas de fuentes que hace cada asesor (QA 2026-11-10, Q-08, D-2)

Colección nueva; no toca `perfiles`. El índice sirve a la lista del asesor: sus consultas, de la más
reciente a la más antigua. Solo expande; `create_index` es idempotente.
"""


def aplicar(db) -> None:
    db["consultas"].create_index([("actor_id", 1), ("creada_en", -1)])
