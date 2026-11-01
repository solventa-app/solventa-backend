"""Mantiene las copias de `contracts/jurisdicciones.json` que llevan las imágenes (T18).

La fuente de verdad es `contracts/jurisdicciones.json`. Las imágenes de BFF, RISK y RATING se
construyen solo con la carpeta de su servicio, así que no ven `contracts/`: cada una lleva una
copia en `app/jurisdicciones.json` (se copia, no se enlaza). Este script la deja idéntica o
verifica que lo sea:

    python scripts/sincronizar_jurisdicciones.py             # copia el contrato a cada servicio
    python scripts/sincronizar_jurisdicciones.py --verificar # falla si una copia difiere (CI)

Cambiar un límite (p. ej. el plazo): editar `contracts/jurisdicciones.json` y correr este script."""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "contracts" / "jurisdicciones.json"
COPIAS = [
    RAIZ / "services" / servicio / "app" / "jurisdicciones.json"
    for servicio in (
        "bff",
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
            print(f"ERROR: {copia.relative_to(RAIZ)} difiere de contracts/jurisdicciones.json")
        if distintas:
            print("Corre `python scripts/sincronizar_fuentes.py` y sube el cambio.")
            return 1
        print(f"OK  {len(COPIAS)} copias de jurisdicciones.json iguales al contrato")
        return 0
    for copia in distintas:
        shutil.copyfile(FUENTE, copia)
        print(f"copiado -> {copia.relative_to(RAIZ)}")
    if not distintas:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
