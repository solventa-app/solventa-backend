"""auditoría de las consultas de rutas del mapa de asistencia (HU-M06, contrato v0.51, B8)

- `auditoria_rutas`: un registro por cada consulta de rutas que hace la app al abrir el mapa.
  Fecha, cliente, prestadores, proveedor (`mapbox` o `simulado`), resultado (`OK`, `FALLA`,
  `CIRCUITO_ABIERTO`) y latencia. La ubicación **exacta** del cliente y su **dirección
  aproximada** (Geocoding inverso de Google, en español) van **cifradas** por campo, con el id del
  registro como dueño (regla #6; ajuste A25 del plan: la dirección también es un dato personal).
- Retención de 90 días con un índice TTL sobre `creada_en`. Índice por cliente para auditoría.

Idempotente: `$jsonSchema` con `create_collection` o `collMod`; `create_index` con los mismos
nombres no falla.
"""

TEXTO = {"bsonType": "string"}
RETENCION_SEGUNDOS = 90 * 24 * 3600

VALIDADOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "creada_en",
            "cliente_id",
            "prestador_ids",
            "proveedor",
            "resultado",
            "latencia_ms",
            "ubicacion_cifrada",
        ],
        "properties": {
            "_id": TEXTO,
            "creada_en": {"bsonType": "date"},
            "cliente_id": TEXTO,
            "prestador_ids": {"bsonType": "array", "items": TEXTO, "maxItems": 10},
            "proveedor": {"enum": ["mapbox", "simulado"]},
            "resultado": {"enum": ["OK", "FALLA", "CIRCUITO_ABIERTO"]},
            "latencia_ms": {"bsonType": ["int", "long"], "minimum": 0},
            "ubicacion_cifrada": TEXTO,
            "direccion_aproximada_cifrada": {"bsonType": ["string", "null"]},
        },
    }
}


def aplicar(db) -> None:
    if "auditoria_rutas" in set(db.list_collection_names()):
        db.command("collMod", "auditoria_rutas", validator=VALIDADOR, validationLevel="strict")
    else:
        db.create_collection("auditoria_rutas", validator=VALIDADOR, validationLevel="strict")
    coleccion = db["auditoria_rutas"]
    coleccion.create_index(
        [("creada_en", 1)],
        name="ttl_creada_en",
        unique=False,
        expireAfterSeconds=RETENCION_SEGUNDOS,
    )
    coleccion.create_index(
        [("cliente_id", 1), ("creada_en", -1)], name="ix_cliente_fecha", unique=False
    )
