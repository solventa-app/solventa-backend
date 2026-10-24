"""Informe de despliegue a staging, en Markdown: qué quedó corriendo, dónde y qué hacer ahora.

    python scripts/informe_despliegue.py --sha <sha> --repo <org/repo> --evento <evento> \\
        --needs needs.json --cloud-run cloud-run.json [--run-url URL] [--main-adelante N] \\
        [--tags "sprint-1 ..."] > informe-despliegue.md

Lo arma el job `informe` de deploy-staging.yml, que corre SIEMPRE (también si una etapa falló) y le
pasa: el resultado de cada etapa (`toJSON(needs)`) y el estado REAL de Cloud Run al terminar
(`gcloud run services list --format=json`). Así el informe dice lo que hay desplegado, no lo que se
pretendía desplegar: cada despliegue deja en el servicio la etiqueta `commit-sha`, y con ella se
distingue un servicio promovido de uno cuya revisión nueva quedó sin tráfico.

Solo usa la biblioteca estándar.
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Canales GraphQL del BFF (ver services/bff/app/main.py: `/graphql/{canal}`).
CANALES_BFF = ("web", "app")

# Prefijo del secreto -> (bandera de infra/staging.tfvars que crea el almacén, qué almacén es).
ALMACENES = (
    ("database-url-", "crear_cloud_sql", "Cloud SQL (Postgres)"),
    ("mongo-uri-", "crear_mongo_atlas", "MongoDB Atlas"),
    ("redis-url-", "crear_redis", "Memorystore (Redis)"),
)

ETAPAS = (
    ("infra", "Terraform (infra)", "no se pidió o no cambió `infra/` ni el manifiesto"),
    ("construir", "Construir imágenes", "ningún servicio cambió"),
    ("migrar", "Migrar bases de datos", "ningún servicio con base de datos cambió"),
    ("desplegar", "Desplegar y promover", "ningún servicio cambió"),
)

ICONO = {"success": "✅", "failure": "❌", "cancelled": "🚫", "skipped": "⏭️"}
TEXTO = {"success": "correcta", "failure": "falló", "cancelled": "cancelada", "skipped": "omitida"}


def cargar_json(ruta: str | None, defecto):
    if not ruta or not Path(ruta).is_file():
        return defecto
    texto = Path(ruta).read_text(encoding="utf-8").strip()
    return json.loads(texto) if texto else defecto


def leer_banderas(texto: str) -> dict[str, bool]:
    """`crear_x = true|false` de infra/staging.tfvars."""
    return {
        m.group(1): m.group(2) == "true"
        for m in re.finditer(r"^(crear_\w+)\s*=\s*(true|false)\b", texto, re.M)
    }


def lista_json(valor: str | None) -> list[str]:
    try:
        return list(json.loads(valor)) if valor else []
    except (TypeError, ValueError):
        return []


def resultado(needs: dict, etapa: str) -> str:
    return needs.get(etapa, {}).get("result") or "skipped"


def estado_servicio(manifiesto: dict, cr: dict | None, sha: str, objetivo: bool) -> dict:
    """Estado real de un servicio a partir de lo que dice Cloud Run."""
    nombre = manifiesto["nombre"]
    base = {"nombre": nombre, "publico": bool(manifiesto.get("publico")), "url": "", "commit": ""}
    if cr is None:
        faltante = "no existe en Cloud Run (¿falta `terraform apply`?)"
        return {**base, "icono": "❌", "estado": faltante}

    etiquetas = cr.get("metadata", {}).get("labels") or {}
    commit = etiquetas.get("commit-sha", "")
    estado = cr.get("status", {})
    trafico = estado.get("traffic", [])
    listo_rev = estado.get("latestReadyRevisionName")
    en_ultima = sum(
        t.get("percent", 0)
        for t in trafico
        if t.get("latestRevision") or (listo_rev and t.get("revisionName") == listo_rev)
    )
    creada = estado.get("latestCreatedRevisionName") or listo_rev
    contenedores = cr.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])
    imagen = contenedores[0].get("image", "")
    datos = {**base, "url": estado.get("url", ""), "commit": commit, "imagen": imagen}

    # `gcloud run deploy` deja la etiqueta commit-sha en el servicio aunque la revisión nueva no
    # arranque, y la entrada de tráfico `latestRevision` apunta a la última LISTA, no a la nueva.
    # Por eso la etiqueta sola no prueba nada: hay que comprobar que la creada llegó a estar lista.
    if objetivo and commit == sha and listo_rev and creada != listo_rev:
        return {**datos, "icono": "❌", "revision_fallida": creada,
                "estado": f"la revisión nueva `{creada}` **no llegó a estar lista** (el contenedor "
                          f"no arrancó); sigue sirviendo `{listo_rev}`"}
    if objetivo and commit == sha and en_ultima >= 100:
        return {**datos, "icono": "✅", "estado": "desplegado y promovido (100 % del tráfico)"}
    if objetivo and commit == sha:
        return {**datos, "icono": "⚠️",
                "estado": "revisión nueva creada **sin tráfico** (falló la prueba de humo o la "
                          "promoción): sigue sirviendo la anterior"}
    if objetivo:
        return {**datos, "icono": "❌",
                "estado": "no se desplegó (una etapa anterior falló): sigue la versión anterior"}
    if not commit:
        return {**datos, "icono": "⏭️", "estado": "sin cambios; aún con la imagen de ejemplo"}
    return {**datos, "icono": "⏭️", "estado": "sin cambios en este despliegue"}


def veredicto(needs: dict, servicios: list[dict], hay_objetivo: bool) -> tuple[str, str]:
    etapas = [resultado(needs, e) for e, _, _ in ETAPAS]
    if "failure" in etapas:
        return "❌", "El despliegue **falló**"
    if "cancelled" in etapas:
        return "🚫", "El despliegue se **canceló**"
    objetivo = [s for s in servicios if s["objetivo"]]
    if any(s["icono"] != "✅" for s in objetivo):
        return "⚠️", "El despliegue quedó **incompleto**"
    if not hay_objetivo and resultado(needs, "infra") == "skipped":
        return "⏭️", "**Nada que desplegar**: no cambió ningún servicio ni la infraestructura"
    return "✅", "El despliegue **terminó bien**"


def tabla_etapas(needs: dict) -> list[str]:
    filas = ["| Etapa | Resultado |", "|---|---|"]
    anteriores: list[tuple[str, str]] = []
    for clave, nombre, motivo_omitida in ETAPAS:
        res = resultado(needs, clave)
        nota = ""
        if res == "skipped":
            # Si una etapa previa falló, esta se omitió por eso y no por falta de cambios.
            rota = next((n for e, n in anteriores if resultado(needs, e) == "failure"), None)
            nota = f" (se omitió porque falló «{rota}»)" if rota else f" ({motivo_omitida})"
        anteriores.append((clave, nombre))
        filas.append(f"| {nombre} | {ICONO.get(res, '❔')} {TEXTO.get(res, res)}{nota} |")
    return filas


def tabla_servicios(servicios: list[dict], sha: str) -> list[str]:
    filas = [
        "| Servicio | Estado | Commit | Acceso | URL |",
        "|---|---|---|---|---|",
    ]
    for s in servicios:
        commit = f"`{s['commit'][:7]}`" if s["commit"] else "—"
        acceso = "público" if s["publico"] else "privado (IAM)"
        url = s["url"] or "—"
        marca = " **←** este despliegue" if s["commit"] == sha and s["objetivo"] else ""
        estado = f"{s['icono']} {s['estado']}{marca}"
        filas.append(f"| `{s['nombre']}` | {estado} | {commit} | {acceso} | {url} |")
    return filas


def uso_de_las_apis(servicios: list[dict], repo: str, sha: str) -> list[str]:
    por_nombre = {s["nombre"]: s for s in servicios}
    lineas = ["## Cómo usarlo", ""]
    bff = por_nombre.get("bff")
    if bff and bff["url"]:
        url = bff["url"].rstrip("/")
        lineas += [
            "**API pública (BFF):**",
            "",
            *[f"- GraphQL `{c}`: `{url}/graphql/{c}`" for c in CANALES_BFF],
            f"- Salud: `{url}/health` · Métricas: `{url}/metrics`",
            "",
            "```bash",
            f"curl -s -X POST {url}/graphql/web \\",
            "  -H 'content-type: application/json' -d '{\"query\":\"{ __typename }\"}'",
            "```",
            "",
        ]
    privado = next((s for s in servicios if not s["publico"] and s["url"]), None)
    if privado:
        lineas += [
            "**Servicios privados** (solo los llaman otros servicios o quien tenga "
            "`roles/run.invoker`):",
            "",
            "```bash",
            "curl -H \"Authorization: Bearer $(gcloud auth print-identity-token)\" \\",
            f"  {privado['url']}/health",
            "```",
            "",
        ]
    base = f"https://github.com/{repo}/blob/{sha}/contracts"
    lineas += [
        f"Contratos de este commit: [esquema GraphQL]({base}/graphql/schema.graphql) · "
        f"[eventos]({base}/events).",
        "",
    ]
    return lineas


def que_hacer(ctx: dict) -> list[str]:
    """Lista de acciones ordenada por urgencia; cada una dice qué y por qué."""
    acciones: list[str] = []
    needs, banderas = ctx["needs"], ctx["banderas"]

    if ctx["icono"] in ("❌", "⚠️", "🚫"):
        fallida = next(
            (n for e, n, _ in ETAPAS if resultado(needs, e) in ("failure", "cancelled")), None
        )
        donde = f"la etapa «{fallida}»" if fallida else "los servicios marcados arriba"
        acciones.append(
            f"**Revisa los logs de {donde}** en [la ejecución]({ctx['run_url']}). Lo que no se "
            "promovió sigue sirviendo la versión anterior, así que staging no se cayó."
        )
        acciones.append(
            "Si una versión ya promovida está mal, vuelve a la anterior con "
            "*Actions → Rollback staging* (elige la rama `main`). **No** revierte la "
            "infraestructura: para eso se revierte el PR."
        )

    if resultado(needs, "infra") == "failure":
        acciones.append(
            "Si el log de «Revisar el plan» dice **«No se aplica: el plan destruye o reemplaza "
            "recursos»**, es la guardia de `scripts/revisar_plan.py`: revisa la lista y, si es lo "
            "esperado, relanza *Deploy staging* desde `main` con `infra` y `permitir_destruir` "
            "activados. No se aplicó nada."
        )
    for s in ctx["servicios"]:
        if s.get("revision_fallida"):
            acciones.append(
                f"**`{s['nombre']}` no arrancó.** Lee el motivo en los logs de la revisión: "
                f"`gcloud logging read 'resource.labels.service_name=\"{s['nombre']}\" AND "
                f"resource.labels.revision_name=\"{s['revision_fallida']}\"' --limit 40`. Si dice "
                "«no se ha cargado en Secret Manager», falta el valor real de un secreto "
                "(`infra/README.md`, «Cargar los secretos»)."
            )

    # Servicios cuya base o caché no existe todavía.
    huerfanos: dict[str, list[str]] = {}
    for m in ctx["manifiesto"]:
        for secreto in (m.get("secretos") or {}).values():
            for prefijo, bandera, almacen in ALMACENES:
                if secreto.startswith(prefijo) and not banderas.get(bandera, False):
                    huerfanos.setdefault(f"{almacen}|{bandera}", []).append(m["nombre"])
    if huerfanos:
        detalle = []
        for clave, nombres in huerfanos.items():
            almacen, bandera = clave.split("|")
            unicos = ", ".join(f"`{n}`" for n in dict.fromkeys(nombres))
            detalle.append(f"   - {almacen} (`{bandera} = false`): {unicos}")
        acciones.append(
            "**Faltan almacenes de datos** (activar uno cuesta por hora y se hace con un PR a "
            "`infra/staging.tfvars` que revise el equipo). Estos servicios no podrán atender con "
            "su imagen real hasta que existan:\n" + "\n".join(detalle)
        )

    secretos = sorted({
        s for m in ctx["manifiesto"] for s in (m.get("secretos") or {}).values()
        if s.startswith("clave-")
    })
    if secretos:
        lista = ", ".join(f"`{s}`" for s in secretos)
        acciones.append(
            f"**Confirma que {lista} tienen valor real** en Secret Manager (Terraform solo crea el "
            "contenedor con un valor marcador). Ver «Cargar los secretos» en `infra/README.md`."
        )

    if ctx["main_adelante"] > 0:
        acciones.append(
            f"**Reintegra `main` en `develop`**: `main` tiene {ctx['main_adelante']} commit(s) que "
            "`develop` no tiene. PR `main` → `develop` con *merge commit*; sin él `develop` pierde "
            "las correcciones del release (`docs/convenciones.md`)."
        )
    if ctx["evento"] == "workflow_run" and not ctx["tags"]:
        acciones.append(
            "Si este release cierra un sprint, **pon el tag `sprint-N` sobre "
            f"`{ctx['sha'][:7]}`**. Los tags `sprint-*` no se pueden mover ni borrar."
        )
    if ctx["icono"] == "✅" and resultado(needs, "infra") == "success":
        acciones.append(
            "Terraform se aplicó: el plan aplicado está en el resumen de la etapa **infra** de "
            f"[la ejecución]({ctx['run_url']}). Revísalo si no lo viste antes del merge."
        )
    if not acciones:
        acciones.append("Ninguna: todo quedó desplegado y no hay pendientes.")
    return [f"{i}. {a}" for i, a in enumerate(acciones, 1)]


def construir_informe(args: argparse.Namespace) -> str:
    needs = cargar_json(args.needs, {})
    cloud_run = {s["metadata"]["name"]: s for s in cargar_json(args.cloud_run, [])}
    # Los servicios `dummy` no se despliegan (solo local): no se informan como faltantes.
    manifiesto = [m for m in cargar_json(args.manifiesto, []) if not m.get("dummy")]
    salidas = needs.get("preparar", {}).get("outputs", {})
    objetivos = set(lista_json(salidas.get("servicios")))
    banderas = leer_banderas(Path(args.tfvars).read_text(encoding="utf-8")) if (
        args.tfvars and Path(args.tfvars).is_file()
    ) else {}

    servicios = []
    for m in manifiesto:
        s = estado_servicio(m, cloud_run.get(m["nombre"]), args.sha, m["nombre"] in objetivos)
        servicios.append({**s, "objetivo": m["nombre"] in objetivos})
    icono, frase = veredicto(needs, servicios, bool(objetivos))

    disparador = {
        "workflow_run": "CI de un merge a `main` (release u hotfix)",
        "workflow_dispatch": "ejecución manual",
    }.get(args.evento, args.evento)
    ctx = {
        "needs": needs, "servicios": servicios, "banderas": banderas, "manifiesto": manifiesto,
        "icono": icono, "run_url": args.run_url or "#", "sha": args.sha, "evento": args.evento,
        "main_adelante": args.main_adelante, "tags": args.tags.split() if args.tags else [],
    }
    fecha = args.fecha or datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lineas = [
        "# Informe de despliegue — staging",
        "",
        f"## {icono} {frase}",
        "",
        "| | |",
        "|---|---|",
        f"| Commit | [`{args.sha[:7]}`](https://github.com/{args.repo}/commit/{args.sha}) |",
        f"| Disparador | {disparador} |",
        f"| Fecha | {fecha} |",
        f"| Proyecto · región | `{args.proyecto or '—'}` · `{args.region or '—'}` |",
        f"| Ejecución | [ver en GitHub Actions]({args.run_url or '#'}) |",
        "",
        "## Etapas",
        "",
        *tabla_etapas(needs),
        "",
        "## Qué quedó corriendo en staging",
        "",
        "Estado real de Cloud Run al terminar (no lo previsto): la columna *Commit* es el que "
        "trae cada servicio.",
        "",
        *tabla_servicios(servicios, args.sha),
        "",
        *uso_de_las_apis(servicios, args.repo, args.sha),
        "## Qué debes hacer",
        "",
        *que_hacer(ctx),
        "",
        "---",
        "_Generado por `scripts/informe_despliegue.py` (job `informe` de `deploy-staging.yml`). "
        "También se guarda como artefacto de la ejecución._",
        "",
    ]
    return "\n".join(lineas)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sha", required=True)
    p.add_argument("--repo", required=True, help="organización/repositorio")
    p.add_argument("--evento", default="workflow_run")
    p.add_argument("--needs", help="JSON de `toJSON(needs)`")
    p.add_argument("--cloud-run", help="JSON de `gcloud run services list`")
    p.add_argument("--manifiesto", default=str(RAIZ / ".github" / "servicios.json"))
    p.add_argument("--tfvars", default=str(RAIZ / "infra" / "staging.tfvars"))
    p.add_argument("--run-url", default="")
    p.add_argument("--proyecto", default="")
    p.add_argument("--region", default="")
    p.add_argument("--main-adelante", type=int, default=0,
                   help="commits de main que develop no tiene")
    p.add_argument("--tags", default="", help="tags que apuntan al commit, separados por espacio")
    p.add_argument("--fecha", default="", help="solo para pruebas")
    args = p.parse_args(argv[1:])
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(construir_informe(args))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
