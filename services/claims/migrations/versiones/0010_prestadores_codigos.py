"""especialidad y horario de los prestadores como datos (HU-M06, contrato v0.53)

`prestadores` gana `codigo_especialidad` (código que la app traduce) y `horario_atencion` (días y
horas, o abierto 24 h). No son obligatorios: los documentos de la versión anterior siguen siendo
válidos hasta que `migrations.catalogos` los complete (corre justo después, en el mismo comando).
`especialidad` y `horario` (texto en español) se quedan para las versiones de la app ya instaladas.

Idempotente: vuelve a fijar el `$jsonSchema` con `collMod` (o crea la colección si no existe).
"""

import copy
import importlib

TEXTO_O_NULO = {"bsonType": ["string", "null"]}
ESPECIALIDADES = [
    "URGENCIAS",
    "MEDICINA_GENERAL",
    "MECANICA_GENERAL",
    "LLANTAS_Y_FRENOS",
    "GRUA_LIVIANA",
    "GRUA_Y_CARRO_TALLER",
]
DIAS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]
DIA_O_NULO = {"enum": [*DIAS, None]}

HORARIO = {
    "bsonType": ["object", "null"],
    "required": ["abierto_24h"],
    "properties": {
        "abierto_24h": {"bsonType": "bool"},
        "dia_desde": DIA_O_NULO,
        "dia_hasta": DIA_O_NULO,
        "abre": TEXTO_O_NULO,
        "cierra": TEXTO_O_NULO,
    },
}


def validador() -> dict:
    """El de `0007_prestadores_detalle` con los dos campos nuevos."""
    v = copy.deepcopy(
        importlib.import_module("migrations.versiones.0007_prestadores_detalle").VALIDADOR
    )
    propiedades = v["$jsonSchema"]["properties"]
    propiedades["codigo_especialidad"] = {"enum": ESPECIALIDADES}
    propiedades["horario_atencion"] = HORARIO
    return v


def aplicar(db) -> None:
    if "prestadores" in set(db.list_collection_names()):
        db.command("collMod", "prestadores", validator=validador(), validationLevel="strict")
    else:
        db.create_collection("prestadores", validator=validador(), validationLevel="strict")
