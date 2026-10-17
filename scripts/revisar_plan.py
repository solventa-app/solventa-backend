"""Revisa un plan de Terraform (JSON) antes de que el pipeline lo aplique.

    terraform show -json plan.bin > plan.json
    python scripts/revisar_plan.py plan.json [--permitir-destruir]

Imprime un resumen en Markdown (el pipeline lo agrega a $GITHUB_STEP_SUMMARY) y termina con 1 si
el plan destruye o reemplaza algo sin `--permitir-destruir`: un apply automático no debe borrar
infraestructura sin que una persona lo decida (por ejemplo, un cambio que fuerza recrear la base
de datos). Solo biblioteca estándar.
"""

import json
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


def revisar(plan: dict, permitir_destruir: bool) -> tuple[str, bool]:
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
    if peligrosos and not permitir_destruir:
        puede = False
        lineas += [
            "",
            "**No se aplica:** el plan destruye o reemplaza recursos. Revísalo y, si es lo que "
            "quieres, vuelve a correr el workflow a mano con `permitir_destruir` activado.",
        ]
    elif peligrosos:
        lineas += ["", "Se permite destruir o reemplazar por decisión explícita."]
    return "\n".join(lineas) + "\n", puede


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print("uso: revisar_plan.py plan.json [--permitir-destruir]", file=sys.stderr)
        return 2
    plan = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    resumen, puede = revisar(plan, "--permitir-destruir" in argv)
    print(resumen)
    return 0 if puede else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
