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
COPIAS = [
    RAIZ / "services" / servicio / "app" / "fuentes.json"
    for servicio in (
        "bff",
        "acl-worker",
        "risk",
        "rating",
    )
]


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    contenido = FUENTE.read_bytes()
    distintas = [c for c in COPIAS if not c.is_file() or c.read_bytes() != contenido]
    if verificar:
        for copia in distintas:
            print(f"ERROR: {copia.relative_to(RAIZ)} difiere de contracts/fuentes.json")
        if distintas:
            print("Corre `python scripts/sincronizar_fuentes.py` y sube el cambio.")
            return 1
        print(f"OK  {len(COPIAS)} copias de fuentes.json iguales al contrato")
        return 0
    for copia in distintas:
        shutil.copyfile(FUENTE, copia)
        print(f"copiado -> {copia.relative_to(RAIZ)}")
    if not distintas:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
