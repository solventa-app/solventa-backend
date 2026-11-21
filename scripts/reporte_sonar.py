#!/usr/bin/env python3
"""Reporte de SonarQube Cloud en el resumen del pipeline.

Se corre después de `sonarqube-scan-action`: lee `.scannerwork/report-task.txt`,
espera a que Sonar procese el análisis y escribe en `$GITHUB_STEP_SUMMARY` (y en
`reporte-sonar.md`, que el workflow sube como artefacto) el quality gate, las
métricas principales y los problemas abiertos más graves. Es informativo: nunca
hace fallar el job (si algo no responde, lo dice en el reporte y sale con 0).

Uso: python3 reporte_sonar.py [ruta/a/report-task.txt]
Necesita SONAR_TOKEN. Solo biblioteca estándar.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

METRICAS = [
    ("coverage", "Cobertura", "%"),
    ("new_coverage", "Cobertura del código nuevo", "%"),
    ("bugs", "Bugs", ""),
    ("vulnerabilities", "Vulnerabilidades", ""),
    ("security_hotspots", "Security hotspots", ""),
    ("code_smells", "Code smells", ""),
    ("duplicated_lines_density", "Duplicación", "%"),
    ("ncloc", "Líneas de código", ""),
]
ICONO_GATE = {
    "OK": "✅ Aprobado",
    "ERROR": "❌ No aprobado",
    "WARN": "⚠️ Advertencia",
    "NONE": "— Sin quality gate",
}
SEVERIDADES = "BLOCKER,CRITICAL,MAJOR"


def leer_tarea(ruta: str) -> dict[str, str]:
    datos: dict[str, str] = {}
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            if "=" in linea:
                k, v = linea.strip().split("=", 1)
                datos[k] = v
    return datos


def pedir(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    cred = base64.b64encode(f"{token}:".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (URL del propio Sonar)
        return json.load(r)


def api(servidor: str, ruta: str, token: str, **params: str) -> dict:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    return pedir(f"{servidor}/api/{ruta}?{q}", token)


def esperar_tarea(url: str, token: str, limite_s: int = 300) -> dict:
    fin = time.time() + limite_s
    while True:
        tarea = pedir(url, token)["task"]
        if tarea["status"] not in ("PENDING", "IN_PROGRESS") or time.time() > fin:
            return tarea
        time.sleep(5)


def generar(ruta_tarea: str, token: str) -> str:
    t = leer_tarea(ruta_tarea)
    servidor = t["serverUrl"].rstrip("/")
    proyecto = t["projectKey"]
    tablero = t.get("dashboardUrl", "")
    tarea = esperar_tarea(t["ceTaskUrl"], token)
    if tarea["status"] != "SUCCESS":
        estado = tarea["status"]
        return (
            f"## Sonar · {proyecto}\n\nEl análisis quedó en `{estado}`. [Ver en Sonar]({tablero})\n"
        )
    rama = tarea.get("branch", "") if not tarea.get("pullRequest") else ""
    pr = tarea.get("pullRequest", "")
    alcance = {"branch": rama, "pullRequest": pr}

    gate = api(servidor, "qualitygates/project_status", token, analysisId=tarea["analysisId"])
    gate = gate["projectStatus"]
    medidas = api(
        servidor,
        "measures/component",
        token,
        component=proyecto,
        metricKeys=",".join(m for m, _, _ in METRICAS),
        **alcance,
    )["component"].get("measures", [])
    valores = {}
    for m in medidas:
        v = m.get("value")
        if v is None and m.get("period"):
            v = m["period"].get("value")
        if v is None and m.get("periods"):
            v = m["periods"][0].get("value")
        valores[m["metric"]] = v
    problemas = api(
        servidor,
        "issues/search",
        token,
        componentKeys=proyecto,
        resolved="false",
        severities=SEVERIDADES,
        ps="10",
        s="SEVERITY",
        asc="false",
        **alcance,
    )

    estado_gate = gate.get("status", "NONE")
    donde = f"PR #{pr}" if pr else f"rama `{rama}`" if rama else "rama principal"
    lineas = [
        f"## Sonar · {proyecto} ({donde})",
        "",
        f"**Quality gate:** {ICONO_GATE.get(estado_gate, estado_gate)} · [Ver en Sonar]({tablero})",
        "",
    ]
    fallidas = [c for c in gate.get("conditions", []) if c.get("status") == "ERROR"]
    if fallidas:
        lineas += ["Condiciones que fallan:", ""]
        for c in fallidas:
            umbral = f"{c.get('comparator')} {c.get('errorThreshold')}"
            lineas.append(f"- `{c['metricKey']}`: {c.get('actualValue')} (umbral {umbral})")
        lineas.append("")
    lineas += ["| Métrica | Valor |", "|---|---|"]
    for clave, nombre, unidad in METRICAS:
        v = valores.get(clave)
        if v is not None:
            lineas.append(f"| {nombre} | {v}{unidad} |")
    total = problemas.get("total", 0)
    lineas += ["", f"**Problemas abiertos graves ({SEVERIDADES.lower()}):** {total}", ""]
    for i in problemas.get("issues", []):
        archivo = i.get("component", "").split(":", 1)[-1]
        linea = f":{i['line']}" if i.get("line") else ""
        tipo = i.get("type", "").lower()
        lineas.append(f"- **{i.get('severity')}** {tipo} · `{archivo}{linea}` · {i.get('message')}")
    return "\n".join(lineas) + "\n"


def main() -> int:
    ruta = sys.argv[1] if len(sys.argv) > 1 else ".scannerwork/report-task.txt"
    token = os.environ.get("SONAR_TOKEN", "")
    try:
        if not token or not os.path.exists(ruta):
            texto = "## Sonar\n\nNo hubo análisis (sin `SONAR_TOKEN` o sin `report-task.txt`).\n"
        else:
            texto = generar(ruta, token)
    except Exception as e:  # informativo: nunca rompe el pipeline
        texto = f"## Sonar\n\nNo se pudo armar el reporte: `{type(e).__name__}`.\n"
    with open("reporte-sonar.md", "w", encoding="utf-8") as f:
        f.write(texto)
    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen:
        with open(resumen, "a", encoding="utf-8") as f:
            f.write(texto)
    print(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
