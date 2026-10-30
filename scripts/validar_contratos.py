"""Valida los contratos: el esquema GraphQL compila, cada evento es un JSON Schema válido, el
OpenAPI de socios (HU-W07) es coherente y `salud.json` suma 100 con micros que existen en
`.github/servicios.json`."""

import json
import sys
from pathlib import Path

from graphql import build_schema
from jsonschema import Draft202012Validator

RAIZ = Path(__file__).resolve().parent.parent / "contracts"
SERVICIOS = RAIZ.parent / ".github" / "servicios.json"


def _refs(nodo):
    """Todas las `$ref` del documento."""
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if clave == "$ref" and isinstance(valor, str):
                yield valor
            else:
                yield from _refs(valor)
    elif isinstance(nodo, list):
        for valor in nodo:
            yield from _refs(valor)


def validar_openapi(ruta: Path) -> None:
    """OpenAPI 3.1 de socios: las `$ref` resuelven, los esquemas son JSON Schema 2020-12, cada
    operación tiene un `operationId` único y todas las rutas van bajo `/v1/`."""
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    if doc.get("openapi") != "3.1.0":
        raise ValueError("debe declarar openapi 3.1.0")
    for ref in set(_refs(doc)):
        nodo = doc
        for parte in ref.removeprefix("#/").split("/"):
            if not isinstance(nodo, dict) or parte not in nodo:
                raise ValueError(f"$ref sin destino: {ref}")
            nodo = nodo[parte]
    for nombre, esquema in doc["components"]["schemas"].items():
        try:
            Draft202012Validator.check_schema(esquema)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"esquema {nombre}: {e}") from e
    vistos: set[str] = set()
    for camino, operaciones in doc["paths"].items():
        if not camino.startswith("/v1/") or camino.startswith("/v1/graphql"):
            raise ValueError(f"{camino}: las rutas de socios van bajo /v1/ y fuera de /v1/graphql")
        for metodo, operacion in operaciones.items():
            op_id = operacion.get("operationId")
            if not op_id or op_id in vistos:
                raise ValueError(f"{metodo.upper()} {camino}: operationId ausente o repetido")
            vistos.add(op_id)
            if not operacion.get("responses"):
                raise ValueError(f"{metodo.upper()} {camino}: sin respuestas")


def validar_salud(ruta: Path) -> None:
    """Los pesos suman 100, cada micro existe en `.github/servicios.json` y las APIs solo usan
    micros con peso."""
    salud = json.loads(ruta.read_text(encoding="utf-8"))
    pesos = {m["nombre"]: m["peso"] for m in salud["micros"]}
    if sum(pesos.values()) != 100:
        raise ValueError(f"los pesos suman {sum(pesos.values())}, no 100")
    existentes = {s["nombre"] for s in json.loads(SERVICIOS.read_text(encoding="utf-8"))}
    if faltan := sorted(set(pesos) - existentes):
        raise ValueError(f"micros que no están en .github/servicios.json: {faltan}")
    for api in salud["apis"]:
        if sin_peso := sorted(set(api["micros"]) - set(pesos)):
            raise ValueError(f"la API {api['nombre']} usa micros sin peso: {sin_peso}")
        if not api["externas"]:
            raise ValueError(f"la API {api['nombre']} no declara APIs externas")
    if salud["ventana_por_defecto"] not in salud["ventanas"]:
        raise ValueError("ventana_por_defecto no está en ventanas")
    if not salud["colores"]["verde"] > salud["colores"]["amarillo"] > 0:
        raise ValueError("los umbrales de color deben cumplir verde > amarillo > 0")
    formula = salud["formula"]
    if formula["peso_disponibilidad"] + formula["peso_latencia"] != 100:
        raise ValueError("peso_disponibilidad + peso_latencia debe ser 100")


def main() -> int:
    errores: list[str] = []

    # schema.graphql (BFF) y cualquier otro contrato GraphQL que se publique en contracts/graphql/.
    for ruta_schema in sorted((RAIZ / "graphql").glob("*.graphql")):
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

    for ruta, validar in [(RAIZ / "salud.json", validar_salud)] + [
        (r, validar_openapi) for r in sorted((RAIZ / "openapi").glob("*.json"))
    ]:
        try:
            validar(ruta)
            print(f"OK  {ruta.relative_to(RAIZ.parent)}")
        except Exception as e:  # noqa: BLE001
            errores.append(f"{ruta}: {e}")

    for e in errores:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
