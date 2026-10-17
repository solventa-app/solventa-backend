"""Revisa un plan de Terraform (JSON) antes de que el pipeline lo aplique.

    terraform show -json plan.bin > plan.json
    python scripts/revisar_plan.py plan.json [--permitir-destruir]
    python scripts/revisar_plan.py plan.json --solo-destruir PATRON [--solo-destruir PATRON ...]

Imprime un resumen en Markdown (el pipeline lo agrega a $GITHUB_STEP_SUMMARY) y termina con 1 si
el plan destruye o reemplaza algo sin permiso: un apply automático no debe borrar
infraestructura sin que una persona lo decida (por ejemplo, un cambio que fuerza recrear la base
de datos).

Hay dos formas de dar permiso:
  --permitir-destruir     permiso general, tras revisar el plan a mano.
  --solo-destruir PATRON  permiso acotado: se puede DESTRUIR lo que case con alguno de los
                          patrones (expresiones regulares sobre la dirección del recurso), pero
                          ningún reemplazo ni nada más. Es lo que usa el botón de apagar costos:
                          borra Redis y su conector y nada distinto.
Solo biblioteca estándar.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Acciones de Terraform que dejan algo sin tocar o solo lo leen.
INOCUAS = {("no-op",), ("read",)}


def clasificar(acciones: list[str]) -> str:
    """Una etiqueta legible para la lista de acciones de un recurso."""
    if "delete" in acciones and "create" in acciones:
        return "reemplazar"
    if "delete" in acciones:
        return "destruir"
    if "create" in acciones:
        return "crear"
    if "update" in acciones:
        return "modificar"
    return "otro"


def revisar(
    plan: dict, permitir_destruir: bool, solo_destruir: tuple[str, ...] = ()
) -> tuple[str, bool]:
    """Devuelve (resumen en Markdown, si el plan puede aplicarse)."""
    cambios = [
        (r["address"], clasificar(r["change"]["actions"]))
        for r in plan.get("resource_changes", [])
        if tuple(r["change"]["actions"]) not in INOCUAS
    ]
    # "forget" solo saca el recurso del estado; no toca nada en GCP.
    cambios = [(a, e) for a, e in cambios if e != "otro"]
    peligrosos = [(a, e) for a, e in cambios if e in ("destruir", "reemplazar")]

    lineas = ["### Plan de Terraform", ""]
    if not cambios:
        lineas.append("Sin cambios: la infraestructura ya coincide con el código.")
        return "\n".join(lineas) + "\n", True

    etiquetas = ("crear", "modificar", "reemplazar", "destruir")
    conteo = {e: sum(1 for _, x in cambios if x == e) for e in etiquetas}
    lineas.append(" · ".join(f"**{n}** {e}" for e, n in conteo.items() if n))
    lineas += ["", "| Acción | Recurso |", "|---|---|"]
    lineas += [f"| {e} | `{a}` |" for a, e in cambios]

    puede = True
    if peligrosos and permitir_destruir:
        lineas += ["", "Se permite destruir o reemplazar por decisión explícita."]
    elif peligrosos and solo_destruir:
        patrones = [re.compile(p) for p in solo_destruir]
        # Con permiso acotado solo vale DESTRUIR lo permitido: un reemplazo (borrar y volver a
        # crear) nunca.
        no_permitidos = [
            (a, e)
            for a, e in peligrosos
            if e != "destruir" or not any(p.search(a) for p in patrones)
        ]
        if no_permitidos:
            puede = False
            lineas += [
                "",
                "**No se aplica:** el plan destruye o reemplaza algo que este permiso "
                "acotado no cubre:",
                *[f"- {e}: `{a}`" for a, e in no_permitidos],
            ]
        else:
            lineas += ["", "Se destruye solo lo que cubre el permiso acotado (apagar costos)."]
    elif peligrosos:
        puede = False
        lineas += [
            "",
            "**No se aplica:** el plan destruye o reemplaza recursos. Revísalo y, si es lo que "
            "quieres, vuelve a correr el workflow a mano con `permitir_destruir` activado.",
        ]
    return "\n".join(lineas) + "\n", puede


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Revisa un plan de Terraform antes de aplicarlo.")
    p.add_argument("plan", help="plan.json (terraform show -json)")
    p.add_argument("--permitir-destruir", action="store_true")
    p.add_argument("--solo-destruir", action="append", default=[], metavar="PATRON")
    args = p.parse_args(argv[1:])
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    resumen, puede = revisar(plan, args.permitir_destruir, tuple(args.solo_destruir))
    print(resumen)
    return 0 if puede else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
