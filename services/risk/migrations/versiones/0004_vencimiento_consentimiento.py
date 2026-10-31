"""perfiles: índices del consentimiento para la revocación y la purga (W01-A3 a A5, KAN-24)

Los campos `consentimiento_id` y `consentimiento_vence_en` son nuevos y opcionales: los perfiles
anteriores siguen válidos y la purga los trata por su `creado_en`. Solo expande; `create_index` es
idempotente.
"""


def aplicar(db) -> None:
    db["perfiles"].create_index([("consentimiento_vence_en", 1)])
    db["perfiles"].create_index([("creado_en", 1)])
    db["perfiles"].create_index([("cliente_id", 1), ("consentimiento_id", 1)])
