"""asistencias en sitio que pide el cliente desde la app (HU-M06, KAN-265 y KAN-266)

- `asistencias`: una por `id_solicitud` (lo genera la app y es el `_id`: así el reenvío no crea
  otra). Lleva una copia del prestador tal como estaba al pedirla. La ubicación del cliente y su
  descripción llegan ya cifradas (`*_cifrada`, AES-GCM por campo con el id como dueño; regla #6).

Idempotente: crea la colección con su `$jsonSchema` o, si ya existe, se lo vuelve a fijar con
`collMod`. Sin índices extra: solo se lee por `_id`.
"""

TEXTO = {"bsonType": "string"}
FECHA = {"bsonType": "date"}
FECHA_O_NULA = {"bsonType": ["date", "null"]}
COORDENADA = {"bsonType": ["double", "int"]}
TIPOS = ["MEDICO", "MECANICO"]

VALIDADOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "cliente_id",
            "prestador",
            "tipo_asistencia",
            "ubicacion_cifrada",
            "estado",
            "intentos",
            "solicitada_en",
            "actualizada_en",
        ],
        "properties": {
            "_id": TEXTO,
            "cliente_id": TEXTO,
            "prestador": {
                "bsonType": "object",
                "required": ["id", "nombre", "tipo", "ciudad", "latitud", "longitud", "activo"],
                "properties": {
                    "id": TEXTO,
                    "nombre": TEXTO,
                    "tipo": {"enum": TIPOS},
                    "ciudad": TEXTO,
                    "latitud": COORDENADA,
                    "longitud": COORDENADA,
                    "activo": {"bsonType": "bool"},
                },
            },
            "tipo_asistencia": {"enum": TIPOS},
            "ubicacion_cifrada": TEXTO,
            "descripcion_cifrada": TEXTO,
            "estado": {"enum": ["CONFIRMADA", "NO_CONFIRMADA", "CANCELADA"]},
            "motivo": {"enum": ["PRESTADOR_SIN_RESPUESTA", None]},
            "minutos_estimados": {"bsonType": ["int", "long", "null"]},
            "intentos": {"bsonType": ["int", "long"], "minimum": 1},
            "solicitada_en": FECHA,
            "actualizada_en": FECHA,
            "cancelada_en": FECHA_O_NULA,
        },
    }
}


def aplicar(db) -> None:
    if "asistencias" in set(db.list_collection_names()):
        db.command("collMod", "asistencias", validator=VALIDADOR, validationLevel="strict")
    else:
        db.create_collection("asistencias", validator=VALIDADOR, validationLevel="strict")
