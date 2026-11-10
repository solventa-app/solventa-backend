"""perfiles_vehiculo: el perfil de riesgo del vehículo para la póliza de auto (KAN-290).

Colección nueva; no toca `perfiles`. Índices para leer el último perfil de un vehículo (RATING, por
la secundaria), para la revocación (titular y consentimiento) y para la purga diaria. Solo expande;
`create_index` es idempotente.
"""


def aplicar(db) -> None:
    coleccion = db["perfiles_vehiculo"]
    coleccion.create_index([("vehiculo_id", 1), ("perfil_version", -1)], unique=True)
    coleccion.create_index([("titular_id", 1), ("consentimiento_id", 1)])
    coleccion.create_index([("consentimiento_vence_en", 1)])
    coleccion.create_index([("creado_en", 1)])
