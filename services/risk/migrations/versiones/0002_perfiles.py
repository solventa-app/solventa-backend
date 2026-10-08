"""perfiles: índice compuesto para la búsqueda de la última versión por cliente (T-W01-7, KAN-24)

RISK es append-only por versión (cada escritura inserta un documento nuevo, nunca sobreescribe).
RATING (`services/rating/app/application/lector_perfiles.py`) y el propio
`services/risk/app/application/repositorio_perfiles.py` necesitan la última versión de un cliente
con `find_one({"cliente_id": ...}, sort=[("perfil_version", -1)])`; sin índice, eso es un escaneo
completo de la colección. `create_index` de pymongo es idempotente (crear un índice igual que ya
existe no falla), así que respeta la regla de `migrations/__init__.py`.
"""


def aplicar(db) -> None:
    db["perfiles"].create_index([("cliente_id", 1), ("perfil_version", -1)])
