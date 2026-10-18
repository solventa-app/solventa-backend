"""cifrar_datos_fuentes: cifra los `datos` existentes en `perfiles` (Bloque 3, EC022).

Busca documentos en `perfiles` cuyas `fuentes` tengan `datos` no vacíos y aún
no tengan `datos_cifrados`, cifra el diccionario `datos` con `CifradorAesGcm`
y limpia `datos: {}`. Idempotente: una segunda corrida no vuelve a cifrar ni
modifica documentos ya cifrados.
"""

import json
from typing import Any

from app.application.cifrado import CifradorAesGcm, CifradorCampos


def aplicar(db: Any, cifrador: CifradorCampos | None = None) -> int:
    cifrador_activo = cifrador or CifradorAesGcm.desde_entorno()
    coleccion = db["perfiles"]
    migrados = 0

    for documento in list(coleccion.find({})):
        fuentes = documento.get("fuentes") or []
        nuevas_fuentes: list[dict[str, Any]] = []
        modificado = False

        for fuente in fuentes:
            copia = dict(fuente)
            datos = copia.get("datos")
            if datos and not copia.get("datos_cifrados"):
                copia["datos_cifrados"] = cifrador_activo.cifrar(
                    json.dumps(datos, ensure_ascii=False, sort_keys=True)
                )
                copia["datos"] = {}
                modificado = True
            nuevas_fuentes.append(copia)

        if modificado:
            coleccion.update_one(
                {"_id": documento["_id"]},
                {"$set": {"fuentes": nuevas_fuentes}},
            )
            migrados += 1

    return migrados
