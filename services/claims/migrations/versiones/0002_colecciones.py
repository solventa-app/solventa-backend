"""colecciones de la mesa de siniestros con validación e índices (HU-W04, KAN-242, W04-2)

Modelo de colecciones referenciadas (decidido por Felipe el 2026-10-28):
- `siniestros`: el caso; apunta a la póliza de POLICY por `poliza_id` (otra base: sin llave
  foránea posible).
- `peritos`: catálogo de peritos.
- `evidencias_siniestro`: intermedia siniestro ↔ archivo del almacenamiento (`archivo_id`).
- `verificaciones_siniestro`: verificaciones regulatorias del caso.
- `asignaciones_perito`: intermedia siniestro ↔ perito (una por caso).
- `resoluciones_siniestro`: la aprobación o el rechazo (una por caso).

Las asignaciones y las resoluciones son append-only: el repositorio no tiene operaciones para
cambiarlas ni borrarlas. Los datos personales llegan ya cifrados (`*_cifrado`, AES-GCM por campo;
regla #6).

Idempotente: crea la colección con su `$jsonSchema` o, si ya existe, se lo vuelve a fijar con
`collMod`; `create_index` con el mismo nombre y llaves no falla.
"""

TEXTO = {"bsonType": "string"}
TEXTO_O_NULO = {"bsonType": ["string", "null"]}
FECHA = {"bsonType": "date"}
FECHA_O_NULA = {"bsonType": ["date", "null"]}
MONTO = {
    "bsonType": "object",
    "required": ["valor", "moneda"],
    "properties": {
        "valor": {"bsonType": "string", "pattern": "^[0-9]+(\\.[0-9]+)?$"},
        "moneda": {"bsonType": "string", "minLength": 3, "maxLength": 3},
    },
}

ESTADOS = ["PENDIENTE", "EN_REVISION", "APROBADO", "RECHAZADO"]
MOTIVOS = [
    "EVIDENCIA_INSUFICIENTE",
    "FUERA_DE_COBERTURA",
    "POLIZA_NO_VIGENTE",
    "INCONSISTENCIA_EN_RELATO",
    "OTRO",
]


def _esquema(requeridos: list[str], propiedades: dict) -> dict:
    return {
        "$jsonSchema": {"bsonType": "object", "required": requeridos, "properties": propiedades}
    }


COLECCIONES = {
    "siniestros": _esquema(
        [
            "_id",
            "radicado",
            "poliza_id",
            "estado",
            "asegurado_nombre_cifrado",
            "asegurado_documento_cifrado",
            "cobertura_aplicada",
            "entidad_acreedora",
            "ocurrido_en",
            "notificado_en",
            "ubicacion_cifrada",
            "descripcion_cifrada",
            "suma_reclamada",
            "vence_dictamen_en",
            "creado_en",
            "actualizado_en",
        ],
        {
            "_id": TEXTO,
            "radicado": TEXTO,
            "poliza_id": TEXTO,
            "estado": {"enum": ESTADOS},
            "asegurado_nombre_cifrado": TEXTO,
            "asegurado_documento_cifrado": TEXTO,
            "cobertura_aplicada": TEXTO,
            "entidad_acreedora": TEXTO,
            "ocurrido_en": FECHA,
            "notificado_en": FECHA,
            "ubicacion_cifrada": TEXTO,
            "descripcion_cifrada": TEXTO,
            "suma_reclamada": MONTO,
            "vence_dictamen_en": FECHA,
            "creado_en": FECHA,
            "actualizado_en": FECHA,
        },
    ),
    "peritos": _esquema(
        [
            "_id",
            "nombre",
            "ciudad",
            "red",
            "registro",
            "tiempo_respuesta_dias",
            "activo",
            "creado_en",
        ],
        {
            "_id": TEXTO,
            "nombre": TEXTO,
            "ciudad": TEXTO,
            "red": TEXTO,
            "registro": TEXTO,
            "tiempo_respuesta_dias": {"bsonType": "int", "minimum": 1},
            "activo": {"bsonType": "bool"},
            "creado_en": FECHA,
        },
    ),
    "evidencias_siniestro": _esquema(
        [
            "_id",
            "siniestro_id",
            "archivo_id",
            "tipo",
            "titulo",
            "nombre_archivo",
            "registrada_en",
            "geoetiquetada",
            "creado_en",
        ],
        {
            "_id": TEXTO,
            "siniestro_id": TEXTO,
            "archivo_id": TEXTO,
            "tipo": {"enum": ["FOTO", "DOCUMENTO"]},
            "titulo": TEXTO,
            "nombre_archivo": TEXTO,
            "registrada_en": FECHA,
            "geoetiquetada": {"bsonType": "bool"},
            "latitud": {"bsonType": ["double", "null"]},
            "longitud": {"bsonType": ["double", "null"]},
            "nota": TEXTO_O_NULO,
            "creado_en": FECHA,
        },
    ),
    "verificaciones_siniestro": _esquema(
        ["_id", "siniestro_id", "orden", "descripcion", "cumple", "creado_en"],
        {
            "_id": TEXTO,
            "siniestro_id": TEXTO,
            "orden": {"bsonType": "int", "minimum": 1},
            "descripcion": TEXTO,
            "cumple": {"bsonType": "bool"},
            "creado_en": FECHA,
        },
    ),
    "asignaciones_perito": _esquema(
        ["_id", "siniestro_id", "perito_id", "notificacion", "asignada_en", "vence_informe_en"],
        {
            "_id": TEXTO,
            "siniestro_id": TEXTO,
            "perito_id": TEXTO,
            "instrucciones_cifradas": TEXTO_O_NULO,
            "notificacion": TEXTO,
            "asignada_en": FECHA,
            "vence_informe_en": FECHA,
            "actor_usuario_id": TEXTO_O_NULO,
        },
    ),
    "resoluciones_siniestro": _esquema(
        ["_id", "siniestro_id", "tipo", "notificacion", "resuelta_en"],
        {
            "_id": TEXTO,
            "siniestro_id": TEXTO,
            "tipo": {"enum": ["APROBACION", "RECHAZO"]},
            "motivo": {"enum": [*MOTIVOS, None]},
            "comentario_cifrado": TEXTO_O_NULO,
            "notificacion": TEXTO,
            "via_recurso": TEXTO_O_NULO,
            "indemnizacion": {"anyOf": [MONTO, {"bsonType": "null"}]},
            "metodo_transferencia": TEXTO_O_NULO,
            "plazo_dispersion": TEXTO_O_NULO,
            "orden_pago_id": TEXTO_O_NULO,
            "resuelta_en": FECHA,
            "actor_usuario_id": TEXTO_O_NULO,
        },
    ),
}

INDICES = {
    "siniestros": [
        ("ux_siniestros_radicado", [("radicado", 1)], True),
        ("ix_siniestros_estado_notificado", [("estado", 1), ("notificado_en", -1)], False),
        ("ix_siniestros_notificado", [("notificado_en", -1)], False),
        ("ix_siniestros_poliza", [("poliza_id", 1)], False),
    ],
    "peritos": [("ux_peritos_registro", [("registro", 1)], True)],
    "evidencias_siniestro": [
        ("ux_evidencias_siniestro_archivo", [("siniestro_id", 1), ("archivo_id", 1)], True),
    ],
    "verificaciones_siniestro": [
        ("ux_verificaciones_siniestro_orden", [("siniestro_id", 1), ("orden", 1)], True),
    ],
    "asignaciones_perito": [("ux_asignaciones_perito_siniestro", [("siniestro_id", 1)], True)],
    "resoluciones_siniestro": [
        ("ux_resoluciones_siniestro_siniestro", [("siniestro_id", 1)], True)
    ],
}


def aplicar(db) -> None:
    existentes = set(db.list_collection_names())
    for nombre, validador in COLECCIONES.items():
        if nombre in existentes:
            db.command("collMod", nombre, validator=validador, validationLevel="strict")
        else:
            db.create_collection(nombre, validator=validador, validationLevel="strict")
        for indice, llaves, unico in INDICES.get(nombre, []):
            db[nombre].create_index(llaves, name=indice, unique=unico)
