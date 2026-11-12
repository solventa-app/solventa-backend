"""detalle de los prestadores y tipo grúa (HU-M06, contrato v0.49)

`prestadores` gana el tipo `GRUA` y los campos que muestra el detalle del prestador en la app:
`aliado` (aliado Solventa o no), `especialidad`, `direccion`, `horario` y `foto_url`. Los nuevos
no son obligatorios en el validador: los documentos de la versión anterior siguen siendo válidos
hasta que `migrations.catalogos` los complete (corre justo después, en el mismo comando).

Idempotente: vuelve a fijar el `$jsonSchema` con `collMod` (o crea la colección si no existe).
"""

TEXTO = {"bsonType": "string"}
TEXTO_O_NULO = {"bsonType": ["string", "null"]}
COORDENADA = {"bsonType": ["double", "int"]}
TIPOS = ["MEDICO", "MECANICO", "GRUA"]

VALIDADOR = {
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
            "aliado": {"bsonType": "bool"},
            "especialidad": TEXTO,
            "direccion": TEXTO,
            "horario": TEXTO_O_NULO,
            "foto_url": TEXTO_O_NULO,
        },
    }
}


def aplicar(db) -> None:
    if "prestadores" in set(db.list_collection_names()):
        db.command("collMod", "prestadores", validator=VALIDADOR, validationLevel="strict")
    else:
        db.create_collection("prestadores", validator=VALIDADOR, validationLevel="strict")
