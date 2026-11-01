"""Mantiene las copias de `contracts/fuentes.json` que llevan las imágenes (Bloque 5).

La fuente de verdad es `contracts/fuentes.json`. Las imágenes de BFF, ACL Worker, RISK y RATING se
construyen solo con la carpeta de su servicio, así que no ven `contracts/`: cada una lleva una
copia en `app/fuentes.json` (se copia, no se enlaza). Este script la deja idéntica o verifica que
lo sea:

    python scripts/sincronizar_fuentes.py             # copia el contrato a cada servicio
    python scripts/sincronizar_fuentes.py --verificar # falla si una copia difiere (lo corre el CI)

Agregar una fuente = una fila en `contracts/fuentes.json`, un adaptador en el ACL y este script."""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "contracts" / "fuentes.json"
SESION = RAIZ / "contracts" / "sesion.json"  # T22: duraciones de la sesión, la lee AUTH
COPIAS_SESION = [RAIZ / "services" / "auth" / "app" / "sesion.json"]
COPIAS = [
    RAIZ / "services" / servicio / "app" / "fuentes.json"
    for servicio in (
        "bff",
        "acl-worker",
        "risk",
        "rating",
    )
]


PARES = [(FUENTE, COPIAS), (SESION, COPIAS_SESION)]


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    fallo = False
    for origen, copias in PARES:
        contenido = origen.read_bytes()
        distintas = [c for c in copias if not c.is_file() or c.read_bytes() != contenido]
        nombre = origen.relative_to(RAIZ)
        if verificar:
            for copia in distintas:
                print(f"ERROR: {copia.relative_to(RAIZ)} difiere de {nombre}")
            if distintas:
                fallo = True
            else:
                print(f"OK  {len(copias)} copias de {origen.name} iguales al contrato")
            continue
        for copia in distintas:
            shutil.copyfile(origen, copia)
            print(f"copiado -> {copia.relative_to(RAIZ)}")
        if not distintas:
            print(f"Sin cambios: las copias de {origen.name} ya eran iguales")
    if fallo:
        print("Corre `python scripts/sincronizar_fuentes.py` y sube el cambio.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
