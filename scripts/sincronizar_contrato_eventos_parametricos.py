"""Mantiene la copia de `contracts/events/evento.parametrico.recibido.schema.json` que lleva la
imagen del ACL Worker (KAN-259, T-M04-5, Fase 3).

El ACL Worker valida cada evento que publica contra ese esquema (`jsonschema`, mismo patrón que
`enviar-push` con `notificacion.push-solicitada`). La fuente de verdad es `contracts/`; la imagen
se construye solo con la carpeta del servicio, así que lleva una copia en
`services/acl-worker/app/` (se copia, no se enlaza), igual que `fuentes.json` y
`fuentes-vehiculo.json`:

    python scripts/sincronizar_contrato_eventos_parametricos.py             # copia el esquema
    python scripts/sincronizar_contrato_eventos_parametricos.py --verificar # falla si difiere (CI)
"""

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = RAIZ / "contracts" / "events" / "evento.parametrico.recibido.schema.json"
COPIA = RAIZ / "services" / "acl-worker" / "app" / "evento.parametrico.recibido.schema.json"


def main(argumentos: list[str]) -> int:
    verificar = "--verificar" in argumentos
    distinta = not COPIA.is_file() or COPIA.read_bytes() != FUENTE.read_bytes()
    nombre = FUENTE.relative_to(RAIZ)
    if verificar:
        if distinta:
            print(f"ERROR: {COPIA.relative_to(RAIZ)} difiere de {nombre}")
            print(
                "Corre `python scripts/sincronizar_contrato_eventos_parametricos.py` "
                "y sube el cambio."
            )
            return 1
        print(f"OK  {COPIA.relative_to(RAIZ)} igual al contrato")
        return 0
    if distinta:
        shutil.copyfile(FUENTE, COPIA)
        print(f"copiado -> {COPIA.relative_to(RAIZ)}")
    else:
        print("Sin cambios: la copia ya era igual")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
