"""Mantiene la copia de dos esquemas de `contracts/events/` que CLAIMS necesita, pero que no son
`siniestro.*` (esos los sincroniza `scripts/sincronizar_eventos_siniestros.py`), en la imagen de
CLAIMS (KAN-259, HU-M04, Fase 4):

- `evento.parametrico.recibido.schema.json`: CLAIMS valida contra él el evento que consume en
  `POST /eventos/parametricos` (lo publica el ACL Worker, Fase 3).
- `notificacion.push-solicitada.schema.json`: CLAIMS lo valida antes de publicar el push que
  liquida la indemnización (lo consume `enviar-push`, Fase 2).

La fuente de verdad es `contracts/events/`; la imagen se construye solo con la carpeta del
servicio, así que lleva una copia en `services/claims/app/eventos/` (se copia, no se enlaza):

    python scripts/sincronizar_evento_parametrico_claims.py             # copia los esquemas
    python scripts/sincronizar_evento_parametrico_claims.py --verificar # falla si difieren (CI)
"""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [
    RAIZ / "contracts" / "events" / "evento.parametrico.recibido.schema.json",
    RAIZ / "contracts" / "events" / "notificacion.push-solicitada.schema.json",
]
DESTINO = RAIZ / "services" / "claims" / "app" / "eventos"


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
                f"ERROR: services/claims/app/eventos/{f.name} difiere de contracts/events/{f.name}"
            )
        if distintas:
            print(
                "Corre `python scripts/sincronizar_evento_parametrico_claims.py` y sube el cambio."
            )
            return 1
        print(f"OK  {len(FUENTES)} esquemas paramétricos iguales al contrato")
        return 0
    DESTINO.mkdir(parents=True, exist_ok=True)
    for f in distintas:
        shutil.copyfile(f, DESTINO / f.name)
        print(f"copiado -> services/claims/app/eventos/{f.name}")
    if not distintas:
        print("Sin cambios: las copias ya eran iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
