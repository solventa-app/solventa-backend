"""Mantiene la copia de los esquemas `siniestro.*` de la imagen de CLAIMS (HU-W04, W04-9).

CLAIMS valida cada evento contra su esquema antes de publicarlo. La fuente de verdad es
`contracts/events/`; la imagen se construye solo con la carpeta del servicio, así que lleva una
copia
en `services/claims/app/eventos/` (se copia, no se enlaza):

    python scripts/sincronizar_eventos_siniestros.py             # copia los esquemas al servicio
    python scripts/sincronizar_eventos_siniestros.py --verificar # falla si una copia difiere (CI)
"""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = sorted((RAIZ / "contracts" / "events").glob("siniestro.*.schema.json"))
DESTINO = RAIZ / "services" / "claims" / "app" / "eventos"


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    distintas = [
        f
        for f in FUENTES
        if not (DESTINO / f.name).is_file() or (DESTINO / f.name).read_bytes() != f.read_bytes()
    ]
    sobrantes = [
        c for c in DESTINO.glob("*.schema.json") if c.name not in {f.name for f in FUENTES}
    ]
    if verificar:
        for f in distintas:
            print(
                f"ERROR: services/claims/app/eventos/{f.name} difiere de contracts/events/{f.name}"
            )
        for c in sobrantes:
            print(f"ERROR: services/claims/app/eventos/{c.name} no existe en contracts/events/")
        if distintas or sobrantes:
            print("Corre `python scripts/sincronizar_eventos_siniestros.py` y sube el cambio.")
            return 1
        print(f"OK  {len(FUENTES)} esquemas de siniestros iguales al contrato")
        return 0
    DESTINO.mkdir(parents=True, exist_ok=True)
    for f in distintas:
        shutil.copyfile(f, DESTINO / f.name)
        print(f"copiado -> services/claims/app/eventos/{f.name}")
    for c in sobrantes:
        c.unlink()
        print(f"borrado  -> services/claims/app/eventos/{c.name}")
    if not distintas and not sobrantes:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
