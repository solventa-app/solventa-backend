"""Mantiene en la imagen del BFF las copias de los contratos de socios (HU-W07, KAN-30).

Las fuentes de verdad son `contracts/openapi/socios-v1.json` (la API REST `/v1` que el BFF sirve
en `/v1/openapi.json`) y `contracts/salud.json` (pesos y reglas de la salud del sistema). La imagen
se construye solo con la carpeta del servicio, así que lleva una copia en `services/bff/app/` (se
copia, no se enlaza):

    python scripts/sincronizar_socios.py             # copia los contratos al BFF
    python scripts/sincronizar_socios.py --verificar # falla si una copia difiere (CI)"""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BFF = RAIZ / "services" / "bff" / "app"
COPIAS = {
    RAIZ / "contracts" / "openapi" / "socios-v1.json": BFF / "socios-v1.json",
    RAIZ / "contracts" / "salud.json": BFF / "salud.json",
}


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    distintas = {
        fuente: copia
        for fuente, copia in COPIAS.items()
        if not copia.is_file() or copia.read_bytes() != fuente.read_bytes()
    }
    if verificar:
        for fuente, copia in distintas.items():
            print(f"ERROR: {copia.relative_to(RAIZ)} difiere de {fuente.relative_to(RAIZ)}")
        if distintas:
            print("Corre `python scripts/sincronizar_socios.py` y sube el cambio.")
            return 1
        print(f"OK  {len(COPIAS)} copias de los contratos de socios iguales al contrato")
        return 0
    for fuente, copia in distintas.items():
        shutil.copyfile(fuente, copia)
        print(f"copiado -> {copia.relative_to(RAIZ)}")
    if not distintas:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
