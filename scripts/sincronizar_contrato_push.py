"""Mantiene la copia del contrato de push en la imagen de `enviar-push` (KAN-259, T-M04-5).

`enviar-push` valida cada evento recibido contra `notificacion.push-solicitada.schema.json` y
resuelve el título/cuerpo con `plantillas-push.json`. La fuente de verdad es `contracts/`; la
imagen se construye solo con la carpeta del servicio, así que lleva una copia en
`services/enviar-push/app/contrato/` (se copia, no se enlaza):

    python scripts/sincronizar_contrato_push.py             # copia el contrato al servicio
    python scripts/sincronizar_contrato_push.py --verificar # falla si una copia difiere (CI)
"""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [
    RAIZ / "contracts" / "events" / "notificacion.push-solicitada.schema.json",
    RAIZ / "contracts" / "plantillas-push.json",
]
DESTINO = RAIZ / "services" / "enviar-push" / "app" / "contrato"


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    distintas = [
        f
        for f in FUENTES
        if not (DESTINO / f.name).is_file() or (DESTINO / f.name).read_bytes() != f.read_bytes()
    ]
    if verificar:
        for f in distintas:
            print(
                f"ERROR: services/enviar-push/app/contrato/{f.name} difiere de contracts/{f.name}"
            )
        if distintas:
            print("Corre `python scripts/sincronizar_contrato_push.py` y sube el cambio.")
            return 1
        print(f"OK  {len(FUENTES)} archivos del contrato de push iguales a contracts/")
        return 0
    DESTINO.mkdir(parents=True, exist_ok=True)
    for f in distintas:
        shutil.copyfile(f, DESTINO / f.name)
        print(f"copiado -> services/enviar-push/app/contrato/{f.name}")
    if not distintas:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
