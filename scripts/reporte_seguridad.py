#!/usr/bin/env python3
"""Reporte consolidado de seguridad (workflow `seguridad.yml`).

Junta en un solo reporte lo que dejan los escaneos del pipeline (cada uno sube su JSON como
artefacto `seguridad-*`) y escribe:

- `reporte-seguridad.md`: resumen con semáforo, tabla por herramienta y hallazgos principales.
  También va al resumen del run (`$GITHUB_STEP_SUMMARY`).
- `reporte-seguridad.json`: todos los hallazgos normalizados, para depurarlos o compararlos.
- `reporte-seguridad.html`: la versión que el workflow convierte a PDF con Chrome sin cabeza.

Es informativo: nunca hace fallar el job. Si un escaneo no dejó resultado, el reporte lo dice.

Uso:
  python3 reporte_seguridad.py consolidar <carpeta-con-resultados> [carpeta-de-salida]
  python3 reporte_seguridad.py headers <url> <salida.json>

Archivos que reconoce `consolidar` (en cualquier subcarpeta):
  gitleaks.json, osv*.json, bandit.json, semgrep.json, trivy-config.json, trivy-imagen-*.json,
  zap*.json, headers.json, actionlint.txt, acciones-sin-sha.txt

Solo biblioteca estándar.
"""

# Las tablas Markdown y la plantilla HTML/CSS se leen mejor en una línea.
# ruff: noqa: E501

from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SEVERIDADES = ["critica", "alta", "media", "baja", "info"]
ETIQUETA = {"critica": "Crítica", "alta": "Alta", "media": "Media", "baja": "Baja", "info": "Info"}
MAX_EN_MD = 10  # hallazgos por herramienta en el resumen del run
MAX_EN_PDF = 150  # hallazgos por herramienta en el PDF (el JSON los lleva todos)

# Qué herramienta corre cada escaneo, en el orden en que aparecen en el reporte.
ESCANEOS = {
    "secretos": ("Secretos", "gitleaks"),
    "dependencias": ("Dependencias (SCA)", "osv-scanner"),
    "sast-bandit": ("Código (SAST)", "Bandit"),
    "sast-semgrep": ("Código (SAST)", "Semgrep"),
    "imagenes": ("Imágenes de contenedor", "Trivy"),
    "iac": ("Infraestructura y Dockerfiles", "Trivy config"),
    "dast": ("Aplicación desplegada (DAST)", "OWASP ZAP"),
    "headers": ("Headers de seguridad", "curl"),
    "workflows": ("Workflows de GitHub Actions", "actionlint"),
}


def _sev_trivy(valor: str) -> str:
    return {"CRITICAL": "critica", "HIGH": "alta", "MEDIUM": "media", "LOW": "baja"}.get(
        valor.upper(), "info"
    )


def _sev_cvss(valor) -> str:
    try:
        puntaje = float(valor)
    except (TypeError, ValueError):
        return "media"  # sin puntaje CVSS: se trata como media hasta revisarlo
    if puntaje >= 9:
        return "critica"
    if puntaje >= 7:
        return "alta"
    if puntaje >= 4:
        return "media"
    return "baja" if puntaje > 0 else "info"


def _cargar(ruta: Path):
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace").strip()
        return json.loads(texto) if texto else None
    except (OSError, json.JSONDecodeError) as error:
        print(f"::warning::No se pudo leer {ruta}: {error}")
        return None


def _hallazgo(escaneo, severidad, titulo, ubicacion="", regla="", detalle="") -> dict:
    return {
        "escaneo": escaneo,
        "severidad": severidad,
        "titulo": " ".join(str(titulo).split())[:300],
        "ubicacion": str(ubicacion)[:300],
        "regla": str(regla)[:120],
        "detalle": " ".join(str(detalle).split())[:500],
    }


# --- Lectores por herramienta -------------------------------------------------------------------


def leer_gitleaks(datos) -> list[dict]:
    salida = []
    for f in datos or []:
        ubicacion = f"{f.get('File', '')}:{f.get('StartLine', '')}"
        commit = (f.get("Commit") or "")[:8]
        salida.append(
            _hallazgo(
                "secretos",
                "alta",
                f.get("Description") or f.get("RuleID"),
                ubicacion,
                f.get("RuleID"),
                f"commit {commit}" if commit else "",
            )
        )
    return salida


def leer_osv(datos) -> list[dict]:
    salida = []
    for resultado in (datos or {}).get("results") or []:
        archivo = (resultado.get("source") or {}).get("path", "")
        for paquete in resultado.get("packages") or []:
            info = paquete.get("package") or {}
            vulns = {v.get("id"): v for v in paquete.get("vulnerabilities") or []}
            grupos = paquete.get("groups") or [{"ids": [i], "max_severity": ""} for i in vulns]
            for grupo in grupos:
                ids = grupo.get("ids") or []
                principal = vulns.get(ids[0], {}) if ids else {}
                alias = [
                    a
                    for a in (grupo.get("aliases") or principal.get("aliases") or [])
                    if a.startswith("CVE-")
                ]
                nombre = f"{info.get('name')}@{info.get('version')}"
                salida.append(
                    _hallazgo(
                        "dependencias",
                        _sev_cvss(grupo.get("max_severity")),
                        f"{nombre}: {principal.get('summary') or ', '.join(ids)}",
                        os.path.relpath(archivo) if archivo.startswith("/") else archivo,
                        ", ".join(alias[:1] + ids[:1]),
                        f"ecosistema {info.get('ecosystem', '')}",
                    )
                )
    return salida


def leer_bandit(datos) -> list[dict]:
    mapa = {"HIGH": "alta", "MEDIUM": "media", "LOW": "baja"}
    return [
        _hallazgo(
            "sast-bandit",
            mapa.get(r.get("issue_severity", ""), "info"),
            r.get("issue_text"),
            f"{r.get('filename')}:{r.get('line_number')}",
            f"{r.get('test_id')} {r.get('test_name', '')}",
            f"confianza {r.get('issue_confidence', '').lower()}",
        )
        for r in (datos or {}).get("results") or []
    ]


def leer_semgrep(datos) -> list[dict]:
    mapa = {"ERROR": "alta", "WARNING": "media", "INFO": "baja"}
    salida = []
    for r in (datos or {}).get("results") or []:
        extra = r.get("extra") or {}
        regla = r.get("check_id", "")
        salida.append(
            _hallazgo(
                "sast-semgrep",
                mapa.get(extra.get("severity", ""), "info"),
                extra.get("message"),
                f"{r.get('path')}:{(r.get('start') or {}).get('line', '')}",
                regla.rsplit(".", 1)[-1],
            )
        )
    return salida


def leer_trivy_config(datos) -> list[dict]:
    salida = []
    for resultado in (datos or {}).get("Results") or []:
        for m in resultado.get("Misconfigurations") or []:
            if m.get("Status", "FAIL") != "FAIL":
                continue
            linea = ((m.get("CauseMetadata") or {}).get("StartLine")) or ""
            salida.append(
                _hallazgo(
                    "iac",
                    _sev_trivy(m.get("Severity", "")),
                    m.get("Title"),
                    f"{resultado.get('Target')}:{linea}".rstrip(":"),
                    m.get("ID"),
                    m.get("Message"),
                )
            )
    return salida


def leer_trivy_imagenes(archivos: list[tuple[str, dict]]) -> list[dict]:
    """Una CVE de la imagen base aparece en casi todos los servicios: se agrupa por (CVE, paquete)."""
    grupos: dict[tuple, dict] = {}
    for servicio, datos in archivos:
        for resultado in (datos or {}).get("Results") or []:
            for v in resultado.get("Vulnerabilities") or []:
                clave = (v.get("VulnerabilityID"), v.get("PkgName"), v.get("InstalledVersion"))
                g = grupos.setdefault(clave, {"v": v, "servicios": set()})
                g["servicios"].add(servicio)
    salida = []
    for (cve, paquete, version), g in grupos.items():
        v = g["v"]
        arreglo = v.get("FixedVersion") or "sin versión corregida"
        servicios = sorted(g["servicios"])
        afectados = ", ".join(servicios) if len(servicios) <= 4 else f"{len(servicios)} imágenes"
        salida.append(
            _hallazgo(
                "imagenes",
                _sev_trivy(v.get("Severity", "")),
                f"{paquete}@{version}: {v.get('Title') or cve}",
                afectados,
                cve,
                f"corregida en {arreglo}",
            )
        )
    return salida


def leer_zap(datos) -> list[dict]:
    mapa = {"3": "alta", "2": "media", "1": "baja", "0": "info"}
    salida = []
    for sitio in (datos or {}).get("site") or []:
        for a in sitio.get("alerts") or []:
            instancias = a.get("instances") or []
            uri = instancias[0].get("uri", "") if instancias else sitio.get("@name", "")
            salida.append(
                _hallazgo(
                    "dast",
                    mapa.get(str(a.get("riskcode")), "info"),
                    a.get("alert") or a.get("name"),
                    uri,
                    f"ZAP {a.get('pluginid', '')}",
                    f"{a.get('count', len(instancias))} ocurrencias",
                )
            )
    return salida


def leer_headers(datos) -> list[dict]:
    if not datos:
        return []
    if datos.get("error"):
        return [
            _hallazgo(
                "headers",
                "info",
                f"No se pudo consultar {datos.get('url')}: {datos['error']}",
                datos.get("url", ""),
            )
        ]
    return [
        _hallazgo(
            "headers",
            f["severidad"],
            f"{f['header']}: {f['problema']}",
            datos.get("url", ""),
            f["header"],
            f.get("recomendacion", ""),
        )
        for f in datos.get("hallazgos") or []
    ]


def leer_actionlint(texto: str) -> list[dict]:
    salida = []
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        ubicacion, _, mensaje = linea.partition(": ")
        regla = mensaje.rsplit("[", 1)[-1].rstrip("]") if mensaje.endswith("]") else ""
        salida.append(_hallazgo("workflows", "media", mensaje, ubicacion, regla))
    return salida


def leer_sin_sha(texto: str) -> list[dict]:
    return [
        _hallazgo(
            "workflows",
            "baja",
            f"Acción sin fijar por SHA: {linea.strip()}",
            "",
            "acciones-sha",
            "Fíjala como dueño/accion@<sha de 40> # vX",
        )
        for linea in sorted(set(texto.splitlines()))
        if linea.strip()
    ]


# --- Consolidación ------------------------------------------------------------------------------


def recolectar(carpeta: Path) -> tuple[list[dict], set[str]]:
    hallazgos: list[dict] = []
    corridos: set[str] = set()
    imagenes: list[tuple[str, dict]] = []
    sencillos = {
        "gitleaks.json": ("secretos", leer_gitleaks),
        "bandit.json": ("sast-bandit", leer_bandit),
        "semgrep.json": ("sast-semgrep", leer_semgrep),
        "trivy-config.json": ("iac", leer_trivy_config),
        "headers.json": ("headers", leer_headers),
    }
    for ruta in sorted(carpeta.rglob("*")):
        if not ruta.is_file():
            continue
        nombre = ruta.name
        if nombre in sencillos:
            escaneo, lector = sencillos[nombre]
            datos = _cargar(ruta)
            # gitleaks escribe "[]" sin hallazgos, pero un archivo vacío también es "sin hallazgos".
            hallazgos += lector(
                datos if datos is not None else ([] if escaneo == "secretos" else None)
            )
            corridos.add(escaneo)
        elif nombre.startswith("osv") and nombre.endswith(".json"):
            hallazgos += leer_osv(_cargar(ruta))
            corridos.add("dependencias")
        elif nombre.startswith("trivy-imagen-") and nombre.endswith(".json"):
            imagenes.append((nombre[len("trivy-imagen-") : -len(".json")], _cargar(ruta)))
        elif nombre.startswith("zap") and nombre.endswith(".json"):
            hallazgos += leer_zap(_cargar(ruta))
            corridos.add("dast")
        elif nombre == "actionlint.txt":
            hallazgos += leer_actionlint(ruta.read_text(encoding="utf-8", errors="replace"))
            corridos.add("workflows")
        elif nombre == "acciones-sin-sha.txt":
            hallazgos += leer_sin_sha(ruta.read_text(encoding="utf-8", errors="replace"))
            corridos.add("workflows")
    if imagenes:
        hallazgos += leer_trivy_imagenes(imagenes)
        corridos.add("imagenes")
    orden = {s: i for i, s in enumerate(SEVERIDADES)}
    hallazgos.sort(
        key=lambda h: (list(ESCANEOS).index(h["escaneo"]), orden[h["severidad"]], h["titulo"])
    )
    return hallazgos, corridos


def conteo(hallazgos: list[dict]) -> dict[str, int]:
    c = dict.fromkeys(SEVERIDADES, 0)
    for h in hallazgos:
        c[h["severidad"]] += 1
    return c


# Puntaje de 0 a 100 (orientativo, como el de MobSF): parte de 100 y resta por severidad, con un tope por
# severidad para que una sola herramienta ruidosa no lo deje en cero. (descuento por hallazgo, tope)
DESCUENTOS = {"critica": (15, 40), "alta": (4, 30), "media": (0.5, 20), "baja": (0.1, 10)}


def puntaje(c: dict[str, int]) -> int:
    descuento = sum(min(c[s] * por_uno, tope) for s, (por_uno, tope) in DESCUENTOS.items())
    return max(0, round(100 - descuento))


# Letra a partir del puntaje, al estilo de la calificación de MobSF en el móvil.
LETRAS = [(90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F")]


def letra(valor: int) -> str:
    return next(nota for minimo, nota in LETRAS if valor >= minimo)


def calificacion(c: dict[str, int]) -> str:
    valor = puntaje(c)
    return f"{valor}/100 ({letra(valor)})"


def semaforo(c: dict[str, int]) -> tuple[str, str]:
    if c["critica"] or c["alta"]:
        return "🔴", "Hay hallazgos críticos o altos por revisar"
    if c["media"]:
        return "🟡", "Sin críticos ni altos; hay hallazgos medios"
    return "🟢", "Sin hallazgos críticos, altos ni medios"


def contexto() -> dict[str, str]:
    servidor = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    return {
        "repositorio": repo or Path.cwd().name,
        "rama": os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME", ""),
        "commit": os.environ.get("GITHUB_SHA", "")[:8],
        "evento": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "fecha": dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).strftime(
            "%Y-%m-%d %H:%M (hora de Bogotá)"
        ),
        "run": f"{servidor}/{repo}/actions/runs/{run}" if repo and run else "",
    }


def _celda(texto: str) -> str:
    return str(texto).replace("|", "\\|").replace("\n", " ")


def escribir_md(hallazgos, corridos, ctx) -> str:
    total = conteo(hallazgos)
    icono, frase = semaforo(total)
    lineas = [f"## {icono} Reporte de seguridad: {calificacion(total)} · {frase}", ""]
    lineas.append(
        f"`{ctx['repositorio']}` · rama `{ctx['rama']}` · commit `{ctx['commit']}` · {ctx['evento']} · {ctx['fecha']}"
    )
    lineas += [
        "",
        "Puntaje orientativo de 0 a 100 (parte de 100 y resta por severidad, con tope; ver `DESCUENTOS`) y letra de A a F (A ≥ 90, B ≥ 75, C ≥ 60, D ≥ 40). Informativo: no bloquea el merge. El PDF, el Markdown y el JSON completo están en el artefacto "
        "`reporte-seguridad`.",
        "",
    ]
    lineas += [
        "| Escaneo | Herramienta | Críticos | Altos | Medios | Bajos | Info | Calificación | Estado |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for clave, (nombre, herramienta) in ESCANEOS.items():
        if clave not in corridos:
            continue
        c = conteo([h for h in hallazgos if h["escaneo"] == clave])
        estado = semaforo(c)[0]
        lineas.append(
            f"| {nombre} | {herramienta} | {c['critica']} | {c['alta']} | {c['media']} | {c['baja']} | "
            f"{c['info']} | {calificacion(c)} | {estado} |"
        )
    lineas.append(
        f"| **Total** | | **{total['critica']}** | **{total['alta']}** | **{total['media']}** | "
        f"**{total['baja']}** | **{total['info']}** | **{calificacion(total)}** | {icono} |"
    )
    sin_correr = [ESCANEOS[k][1] for k in ESCANEOS if k not in corridos]
    if sin_correr:
        lineas += [
            "",
            f"Sin resultado en este run: {', '.join(sin_correr)} (no aplica a este evento o el escaneo falló; "
            "revisa su job).",
        ]
    for clave, (nombre, herramienta) in ESCANEOS.items():
        propios = [h for h in hallazgos if h["escaneo"] == clave and h["severidad"] != "info"]
        if not propios:
            continue
        lineas += [
            "",
            f"### {nombre} ({herramienta}): {len(propios)}",
            "",
            "| Severidad | Hallazgo | Dónde | Regla |",
            "|---|---|---|---|",
        ]
        for h in propios[:MAX_EN_MD]:
            lineas.append(
                f"| {ETIQUETA[h['severidad']]} | {_celda(h['titulo'])} | {_celda(h['ubicacion'])} | "
                f"{_celda(h['regla'])} |"
            )
        if len(propios) > MAX_EN_MD:
            lineas.append(f"| | … y {len(propios) - MAX_EN_MD} más en el artefacto | | |")
    if ctx["run"]:
        lineas += ["", f"[Ver el run]({ctx['run']})"]
    return "\n".join(lineas) + "\n"


def escribir_html(hallazgos, corridos, ctx) -> str:
    e = html.escape
    total = conteo(hallazgos)
    icono, frase = semaforo(total)
    color = {"🔴": "#c62828", "🟡": "#f9a825", "🟢": "#2e7d32"}[icono]
    filas = []
    for clave, (nombre, herramienta) in ESCANEOS.items():
        if clave not in corridos:
            continue
        c = conteo([h for h in hallazgos if h["escaneo"] == clave])
        filas.append(
            f"<tr><td>{e(nombre)}</td><td>{e(herramienta)}</td>"
            + "".join(f"<td class=n>{c[s]}</td>" for s in SEVERIDADES)
            + f"<td class=n>{calificacion(c)}</td><td>{semaforo(c)[0]}</td></tr>"
        )
    filas.append(
        "<tr class=total><td>Total</td><td></td>"
        + "".join(f"<td class=n>{total[s]}</td>" for s in SEVERIDADES)
        + f"<td class=n>{calificacion(total)}</td><td>{icono}</td></tr>"
    )
    secciones = []
    for clave, (nombre, herramienta) in ESCANEOS.items():
        propios = [h for h in hallazgos if h["escaneo"] == clave]
        if not propios:
            continue
        cuerpo = "".join(
            f"<tr><td><span class='sev {h['severidad']}'>{ETIQUETA[h['severidad']]}</span></td>"
            f"<td>{e(h['titulo'])}<div class=det>{e(h['detalle'])}</div></td>"
            f"<td class=mono>{e(h['ubicacion'])}</td><td class=mono>{e(h['regla'])}</td></tr>"
            for h in propios[:MAX_EN_PDF]
        )
        resto = (
            f"<p class=det>… y {len(propios) - MAX_EN_PDF} más en reporte-seguridad.json.</p>"
            if len(propios) > MAX_EN_PDF
            else ""
        )
        secciones.append(
            f"<h2>{e(nombre)} <small>{e(herramienta)} · {len(propios)}</small></h2>"
            "<table class=hall><thead><tr><th>Severidad</th><th>Hallazgo</th><th>Dónde</th><th>Regla</th></tr>"
            f"</thead><tbody>{cuerpo}</tbody></table>{resto}"
        )
    sin_correr = [ESCANEOS[k][1] for k in ESCANEOS if k not in corridos]
    nota = (
        f"<p class=det>Sin resultado en este run: {e(', '.join(sin_correr))}.</p>"
        if sin_correr
        else ""
    )
    return f"""<!doctype html><html lang=es><head><meta charset=utf-8><title>Reporte de seguridad</title>
<style>
@page {{ size: A4; margin: 14mm; }}
body {{ font: 10.5px/1.45 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #1c1c1c; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }} h2 {{ font-size: 14px; margin: 18px 0 6px; break-after: avoid; }}
h2 small {{ color: #666; font-weight: normal; font-size: 11px; }}
.meta {{ color: #555; margin-bottom: 12px; }}
.semaforo {{ border-left: 6px solid {color}; background: #f6f6f6; padding: 8px 12px; font-size: 13px; margin: 10px 0 14px; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ border-bottom: 1px solid #e3e3e3; padding: 4px 6px; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; }} td.n {{ text-align: right; }} tr.total td {{ font-weight: bold; border-top: 2px solid #999; }}
tr {{ break-inside: avoid; }} .mono {{ font-family: ui-monospace, Menlo, monospace; font-size: 9.5px; word-break: break-all; }}
.det {{ color: #666; font-size: 9.5px; }}
.puntaje {{ font-size: 22px; font-weight: bold; margin-right: 10px; }} .puntaje small {{ font-size: 12px; color: #666; }}
.sev {{ display: inline-block; padding: 1px 6px; border-radius: 3px; color: #fff; font-size: 9.5px; }}
.critica {{ background: #7b1fa2; }} .alta {{ background: #c62828; }} .media {{ background: #ef6c00; }}
.baja {{ background: #1565c0; }} .info {{ background: #757575; }}
</style></head><body>
<h1>Reporte de seguridad · {e(ctx["repositorio"])}</h1>
<div class=meta>Rama <b>{e(ctx["rama"])}</b> · commit <b>{e(ctx["commit"])}</b> · evento {e(ctx["evento"])} · {e(ctx["fecha"])}<br>{e(ctx["run"])}</div>
<div class=semaforo><span class=puntaje>{letra(puntaje(total))} · {puntaje(total)}<small>/100</small></span> {icono} <b>{e(frase)}</b>. Reporte informativo: no bloquea el merge.</div>
<table><thead><tr><th>Escaneo</th><th>Herramienta</th><th>Críticos</th><th>Altos</th><th>Medios</th><th>Bajos</th><th>Info</th><th>Calificación</th><th>Estado</th></tr></thead>
<tbody>{"".join(filas)}</tbody></table>{nota}
{"".join(secciones)}
</body></html>
"""


def consolidar(entrada: Path, salida: Path) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    hallazgos, corridos = recolectar(entrada)
    ctx = contexto()
    md = escribir_md(hallazgos, corridos, ctx)
    (salida / "reporte-seguridad.md").write_text(md, encoding="utf-8")
    (salida / "reporte-seguridad.html").write_text(
        escribir_html(hallazgos, corridos, ctx), encoding="utf-8"
    )
    total = conteo(hallazgos)
    (salida / "reporte-seguridad.json").write_text(
        json.dumps(
            {
                "contexto": ctx,
                "puntaje": puntaje(total),
                "letra": letra(puntaje(total)),
                "semaforo": semaforo(total)[0],
                "total": total,
                "escaneos": sorted(corridos),
                "hallazgos": hallazgos,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen:
        with open(resumen, "a", encoding="utf-8") as f:
            f.write(md)
    print(md)


# --- Headers de seguridad -----------------------------------------------------------------------

# (header, severidad si falta, recomendación)
HEADERS = [
    (
        "Content-Security-Policy",
        "media",
        "Define una CSP (default-src 'self' y los orígenes que la app usa).",
    ),
    ("Strict-Transport-Security", "media", "max-age=63072000; includeSubDomains"),
    (
        "X-Frame-Options",
        "media",
        "DENY (o frame-ancestors 'none' en la CSP) para evitar clickjacking.",
    ),
    ("X-Content-Type-Options", "baja", "nosniff"),
    ("Referrer-Policy", "baja", "strict-origin-when-cross-origin"),
    (
        "Permissions-Policy",
        "baja",
        "Desactiva lo que no se usa, p. ej. camera=(), microphone=(), geolocation=()",
    ),
]


def revisar_headers(url: str, destino: Path) -> None:
    resultado: dict = {"url": url, "hallazgos": [], "presentes": {}}
    try:
        peticion = urllib.request.Request(url, headers={"User-Agent": "solventa-seguridad-ci"})
        with urllib.request.urlopen(peticion, timeout=20) as respuesta:
            cabeceras = {k.lower(): v for k, v in respuesta.headers.items()}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        resultado["error"] = str(error)
        destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"::warning::No se pudo consultar {url}: {error}")
        return
    for header, severidad, recomendacion in HEADERS:
        valor = cabeceras.get(header.lower())
        if valor is None:
            if header == "X-Frame-Options" and "frame-ancestors" in cabeceras.get(
                "content-security-policy", ""
            ):
                continue
            resultado["hallazgos"].append(
                {
                    "header": header,
                    "severidad": severidad,
                    "problema": "no está",
                    "recomendacion": recomendacion,
                }
            )
            continue
        resultado["presentes"][header] = valor
        if header == "Content-Security-Policy" and (
            "'unsafe-inline'" in valor or "'unsafe-eval'" in valor
        ):
            resultado["hallazgos"].append(
                {
                    "header": header,
                    "severidad": "baja",
                    "problema": "permite 'unsafe-inline' o 'unsafe-eval'",
                    "recomendacion": "Usa nonces o hashes en vez de unsafe-*.",
                }
            )
    for header in ("server", "x-powered-by"):
        if header in cabeceras and any(c.isdigit() for c in cabeceras[header]):
            resultado["hallazgos"].append(
                {
                    "header": header,
                    "severidad": "info",
                    "problema": f"expone versión ({cabeceras[header]})",
                    "recomendacion": "Oculta la versión del servidor.",
                }
            )
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


def main(argv: list[str]) -> int:
    try:
        if len(argv) >= 2 and argv[0] == "consolidar":
            consolidar(Path(argv[1]), Path(argv[2]) if len(argv) > 2 else Path("."))
        elif len(argv) == 3 and argv[0] == "headers":
            revisar_headers(argv[1], Path(argv[2]))
        else:
            print(__doc__)
            return 2
    except Exception as error:  # informativo: nunca tumba el pipeline
        print(f"::warning::El reporte de seguridad falló: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
