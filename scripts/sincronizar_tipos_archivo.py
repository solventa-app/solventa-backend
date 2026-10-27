"""Mantiene la copia de `contracts/tipos-archivo.json` de la imagen de ALMACENAMIENTO (KAN-250).

La fuente de verdad es `contracts/tipos-archivo.json`. La imagen se construye solo con la carpeta
del servicio, así que lleva una copia en `app/tipos-archivo.json` (se copia, no se enlaza):

    python scripts/sincronizar_tipos_archivo.py             # copia el contrato al servicio
    python scripts/sincronizar_tipos_archivo.py --verificar # falla si la copia difiere (CI)

Agregar un tipo de archivo = una fila en `contracts/tipos-archivo.json` y este script."""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "contracts" / "tipos-archivo.json"
COPIAS = [
    RAIZ / "services" / servicio / "app" / "tipos-archivo.json"
    for servicio in ("almacenamiento",)
]


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    contenido = FUENTE.read_bytes()
    distintas = [c for c in COPIAS if not c.is_file() or c.read_bytes() != contenido]
    if verificar:
        for copia in distintas:
            print(f"ERROR: {copia.relative_to(RAIZ)} difiere de contracts/tipos-archivo.json")
        if distintas:
            print("Corre `python scripts/sincronizar_tipos_archivo.py` y sube el cambio.")
            return 1
        print(f"OK  {len(COPIAS)} copias de tipos-archivo.json iguales al contrato")
        return 0
    for copia in distintas:
        shutil.copyfile(FUENTE, copia)
        print(f"copiado -> {copia.relative_to(RAIZ)}")
    if not distintas:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
