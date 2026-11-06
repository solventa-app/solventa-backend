"""catálogo maestro de prestadores para la asistencia en sitio (HU-M06, KAN-263)

- `prestadores`: clínicas y talleres, sin datos personales. Se dan de baja con `activo: false`,
  nunca se borran.
- `catalogos`: un documento de control por catálogo (`_id: "prestadores"`) con la `version`
  vigente y la `huella` del contenido. La app solo descarga cuando cambia la versión.

Los datos no se cargan aquí sino en `migrations.catalogos`, que corre en cada `python -m
migrations`: así cambiar el catálogo no exige una migración nueva.

Idempotente: crea la colección con su `$jsonSchema` o, si ya existe, se lo vuelve a fijar con
`collMod`. Sin índices: son unas decenas de documentos que se leen completos.
"""

TEXTO = {"bsonType": "string"}
COORDENADA = {"bsonType": ["double", "int"]}
TIPOS = ["MEDICO", "MECANICO"]

COLECCIONES = {
    "prestadores": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "nombre", "tipo", "ciudad", "latitud", "longitud", "activo"],
            "properties": {
                "_id": TEXTO,
                "nombre": TEXTO,
                "tipo": {"enum": TIPOS},
                "ciudad": TEXTO,
                "latitud": {**COORDENADA, "minimum": -90, "maximum": 90},
                "longitud": {**COORDENADA, "minimum": -180, "maximum": 180},
                "activo": {"bsonType": "bool"},
            },
        }
    },
    "catalogos": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "version", "huella", "actualizado_en"],
            "properties": {
                "_id": TEXTO,
                "version": TEXTO,
                "huella": TEXTO,
                "actualizado_en": {"bsonType": "date"},
            },
        }
    },
}


def aplicar(db) -> None:
    existentes = set(db.list_collection_names())
    for nombre, validador in COLECCIONES.items():
        if nombre in existentes:
            db.command("collMod", nombre, validator=validador, validationLevel="strict")
        else:
            db.create_collection(nombre, validator=validador, validationLevel="strict")
