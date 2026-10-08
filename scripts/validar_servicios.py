"""Valida que `.github/servicios.json` (fuente única de servicios) esté al día con el repo.

El manifiesto lo consumen el CI, el deploy y Terraform. La lista de servicios también aparece, por
necesidad, en `ci.yml` (filtros de rutas), `dependabot.yml` y `docker-compose.yml`; este script
falla si alguna se desincroniza, para que agregar un servicio no deje un hueco silencioso en la
automatización.

Solo usa la biblioteca estándar. Uso: `python scripts/validar_servicios.py`
"""

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MANIFIESTO = RAIZ / ".github" / "servicios.json"


def leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8")


def validar() -> list[str]:
    errores: list[str] = []
    servicios = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    por_nombre = {s["nombre"]: s for s in servicios}

    if len(por_nombre) != len(servicios):
        errores.append("hay nombres de servicio repetidos en el manifiesto")
    puertos = [s["puerto"] for s in servicios]
    if len(set(puertos)) != len(puertos):
        errores.append("hay puertos locales repetidos en el manifiesto")

    # Todo directorio con Dockerfile (services/, stubs/) debe estar en el manifiesto, y viceversa.
    en_disco = {
        p.parent.relative_to(RAIZ).as_posix()
        for base in ("services", "stubs")
        for p in (RAIZ / base).glob("*/Dockerfile")
    }
    en_manifiesto = {s["ruta"] for s in servicios}
    for ruta in sorted(en_disco - en_manifiesto):
        errores.append(f"{ruta} tiene Dockerfile pero no está en .github/servicios.json")
    for ruta in sorted(en_manifiesto - en_disco):
        errores.append(f"{ruta} está en el manifiesto pero no tiene Dockerfile")

    ci = leer(".github/workflows/ci.yml")
    dependabot = leer(".github/dependabot.yml")
    compose = leer("docker-compose.yml")

    for s in servicios:
        nombre, ruta, puerto = s["nombre"], s["ruta"], s["puerto"]

        if f"['{ruta}/**']" not in ci:
            errores.append(f"{nombre}: falta el filtro '{ruta}/**' en .github/workflows/ci.yml")
        for ecosistema in ("pip", "docker"):
            linea = f"package-ecosystem: {ecosistema}, directory: /{ruta},"
            if linea not in dependabot:
                errores.append(f"{nombre}: falta la entrada {ecosistema} en dependabot.yml")
        if f"\n  {nombre}:\n" not in compose:
            errores.append(f"{nombre}: no existe el servicio en docker-compose.yml")
        if f'"{puerto}:{puerto}"' not in compose:
            errores.append(f"{nombre}: el puerto {puerto}:{puerto} no está en docker-compose.yml")

        for destino in s.get("llama", []):
            if destino not in por_nombre:
                errores.append(f"{nombre}: llama a '{destino}', que no existe")
            elif "url_env" not in por_nombre[destino]:
                errores.append(f"{destino}: lo llama {nombre} pero no define url_env")

        if "migracion" in s:
            if f"\n  migrar-{nombre}:\n" not in compose:
                errores.append(f"{nombre}: tiene migración y falta migrar-{nombre} en el compose")
            if not (RAIZ / ruta / "migrations").is_dir():
                errores.append(f"{nombre}: tiene migración pero no existe {ruta}/migrations/")
            if not {"DATABASE_URL", "MONGO_URI"} & set(s.get("secretos", {})):
                errores.append(f"{nombre}: tiene migración pero no declara el secreto de su base")

    return errores


def main() -> int:
    errores = validar()
    if errores:
        print("Manifiesto de servicios inconsistente:", file=sys.stderr)
        for e in errores:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("Manifiesto de servicios consistente con ci.yml, dependabot.yml y docker-compose.yml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
