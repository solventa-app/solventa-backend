"""Valida los contratos: el esquema GraphQL compila y cada evento es un JSON Schema válido."""

import json
import sys
from pathlib import Path

from graphql import build_schema
from jsonschema import Draft202012Validator

RAIZ = Path(__file__).resolve().parent.parent / "contracts"


def main() -> int:
    errores: list[str] = []

    ruta_schema = RAIZ / "graphql" / "schema.graphql"
    try:
        build_schema(ruta_schema.read_text(encoding="utf-8"))
        print(f"OK  {ruta_schema.relative_to(RAIZ.parent)}")
    except Exception as e:  # noqa: BLE001 - se reporta cualquier error de sintaxis o de tipos
        errores.append(f"{ruta_schema}: {e}")

    for ruta in sorted((RAIZ / "events").glob("*.schema.json")):
        try:
            esquema = json.loads(ruta.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(esquema)
            if esquema.get("title") != ruta.name.removesuffix(".schema.json"):
                raise ValueError("el 'title' debe coincidir con el nombre del archivo")
            print(f"OK  {ruta.relative_to(RAIZ.parent)}")
        except Exception as e:  # noqa: BLE001
            errores.append(f"{ruta}: {e}")

    for e in errores:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
