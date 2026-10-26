"""Decide qué servicios despliega el pipeline, a partir de `.github/servicios.json`.

    python scripts/seleccionar_servicios.py todos
    python scripts/seleccionar_servicios.py lista auth,risk
    python scripts/seleccionar_servicios.py cambios <base> <head>   # los que cambiaron en git

Imprime `servicios=[...]` y `migraciones=[...]` (nombres, JSON compacto), listos para
`>> $GITHUB_OUTPUT`. `migraciones` es el subconjunto con migración de base de datos.
Solo usa la biblioteca estándar.
"""

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
USO = "uso: seleccionar_servicios.py todos | lista a,b | cambios <base> <head>"


def cargar() -> list[dict]:
    return json.loads((RAIZ / ".github" / "servicios.json").read_text(encoding="utf-8"))


def archivos_cambiados(base: str, head: str) -> list[str]:
    salida = subprocess.run(
        ["git", "diff", "--name-only", base, head],
        cwd=RAIZ, capture_output=True, text=True, check=True,
    )
    return salida.stdout.split()


ARGUMENTOS = {"todos": 0, "lista": 1, "cambios": 2}


def seleccionar(servicios: list[dict], modo: str, args: list[str]) -> list[dict]:
    if ARGUMENTOS.get(modo) != len(args):
        raise SystemExit(USO)
    if modo == "todos":
        return servicios
    if modo == "lista":
        pedidos = [n.strip() for n in args[0].split(",") if n.strip()]
        conocidos = {s["nombre"] for s in servicios}
        desconocidos = sorted(set(pedidos) - conocidos)
        if desconocidos:
            raise SystemExit(f"servicios desconocidos: {', '.join(desconocidos)}")
        return [s for s in servicios if s["nombre"] in pedidos]
    if modo == "cambios":
        cambiados = archivos_cambiados(args[0], args[1])
        return [s for s in servicios if any(a.startswith(s["ruta"] + "/") for a in cambiados)]
    raise SystemExit(USO)


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(USO)
    elegidos = seleccionar(cargar(), argv[0], argv[1:])
    print(f"servicios={json.dumps([s['nombre'] for s in elegidos], separators=(',', ':'))}")
    migran = [s["nombre"] for s in elegidos if "migracion" in s]
    print(f"migraciones={json.dumps(migran, separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
