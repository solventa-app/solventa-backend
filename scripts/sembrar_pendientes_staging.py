"""Agrega siniestros PENDIENTE a la mesa de siniestros de staging (HU-W04) sin tocar los que ya hay.

Por qué existe: la semilla (`python -m app.sembrar`, docs/guia-siembra-siniestros-staging.md) deja
7 pendientes con plazos de 10 a 70 horas, y el job `aprobar-siniestros-vencidos` (cada 15 min, D-3
de la revisión QA) aprueba solo los vencidos. A los pocos días la mesa se queda sin pendientes.
Volver a sembrar reinicia los 20 casos y borra las resoluciones de las demos, así que este script
solo agrega casos nuevos con un plazo de dictamen largo.

Cada caso nuevo copia uno de la semilla (`services/claims/semilla/siniestros.json`): su póliza demo,
asegurado, relato, ubicación, verificaciones y evidencias (reusa el `archivo_id` de la evidencia ya
subida; no sube archivos). Cambia el id (`sin-pend-AAAAMMDD-NN`), toma un radicado nuevo del
contador de CLAIMS y queda PENDIENTE, sin derivación ni resolución. Escribe con el repositorio y el
cifrador de CLAIMS (mismo formato que el servicio), con la llave `clave-cifrado` de Secret Manager.

Es idempotente por día: repetirlo el mismo día no duplica casos. A un caso que sigue PENDIENTE le
renueva el plazo; uno que ya se resolvió no lo toca.

Sin --aplicar es un simulacro. Requisitos: gcloud con acceso a solventa-511815 y, en un entorno
aparte, las dependencias de CLAIMS que usa: `pip install pymongo cryptography httpx`.

Uso (docs/guia-demo-staging.md, sección 8):
    .venv-ops/Scripts/python scripts/sembrar_pendientes_staging.py                       # simulacro
    .venv-ops/Scripts/python scripts/sembrar_pendientes_staging.py --cantidad 6 --dias 10 --aplicar
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CLAIMS = RAIZ / "services" / "claims"
PROYECTO = "solventa-511815"


def secreto(nombre: str) -> str:
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not gcloud:
        sys.exit("No encuentro gcloud en el PATH.")
    salida = subprocess.run(
        [
            gcloud,
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={nombre}",
            f"--project={PROYECTO}",
        ],
        capture_output=True,
        text=True,
    )
    if salida.returncode != 0:
        sys.exit(f"No pude leer el secreto {nombre}: {salida.stderr.strip()}")
    return salida.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cantidad", type=int, default=6, help="casos nuevos (por defecto 6)")
    parser.add_argument(
        "--dias", type=float, default=10, help="plazo del dictamen en días (por defecto 10)"
    )
    parser.add_argument(
        "--aplicar", action="store_true", help="escribe de verdad (sin esto es un simulacro)"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(CLAIMS))
    from pymongo import MongoClient

    from app.application.cifrado import CifradorAesGcm
    from app.application.repositorio import RepositorioSiniestrosMongo

    semilla = json.loads((CLAIMS / "semilla" / "siniestros.json").read_text(encoding="utf-8"))
    ahora = datetime.now(UTC)
    vence = ahora + timedelta(days=args.dias)
    cliente = MongoClient(secreto("mongo-uri-claims"), tz_aware=True)
    repo = RepositorioSiniestrosMongo(
        cliente,
        CifradorAesGcm.desde_entorno(cifrado_clave=secreto("clave-cifrado"), kms_key_name=""),
        "claims",
    )

    creados = renovados = omitidos = 0
    for n in range(1, args.cantidad + 1):
        base = semilla[(n - 1) % len(semilla)]
        sid = f"sin-pend-{ahora:%Y%m%d}-{n:02d}"
        guardado_base = repo.obtener(base["id"], primaria=True)
        if guardado_base is None:
            print(f"{sid}: omitido, {base['id']} no está en staging (corre antes la semilla)")
            omitidos += 1
            continue
        existente = repo.obtener(sid, primaria=True)
        if existente is not None and existente["estado"] != "PENDIENTE":
            print(f"{sid}: omitido, ya está {existente['estado']}")
            omitidos += 1
            continue
        accion = "renovar plazo" if existente else "crear"
        print(f"{sid}: {accion} desde {base['id']} | vence {vence:%Y-%m-%d %H:%M} UTC")
        if not args.aplicar:
            continue
        repo.guardar_siniestro(
            {
                **base,
                "id": sid,
                "radicado": existente["radicado"]
                if existente
                else repo.siguiente_radicado(ahora.year),
                "estado": "PENDIENTE",
                "poliza_id": guardado_base["poliza_id"],
                "ocurrido_en": datetime.fromisoformat(base["ocurrido_en"]),
                "notificado_en": ahora - timedelta(hours=n),
                "vence_dictamen_en": vence,
            },
            ahora,
        )
        for orden, v in enumerate(base["verificaciones"], start=1):
            repo.guardar_verificacion(
                {
                    "id": f"{sid}-ver-{orden}",
                    "siniestro_id": sid,
                    "orden": orden,
                    "descripcion": v["descripcion"],
                    "cumple": v["cumple"],
                    "creado_en": ahora,
                }
            )
        for e in base["evidencias"]:
            original = repo.evidencia(e["id"])
            if original is None:
                continue
            repo.guardar_evidencia(
                {
                    **original,
                    "id": f"{sid}-{e['id']}",
                    "siniestro_id": sid,
                    "creado_en": ahora,
                }
            )
        repo.reiniciar_resolucion(sid, None, None)
        renovados += bool(existente)
        creados += not existente

    cliente.close()
    if args.aplicar:
        print(f"Listo: {creados} creados, {renovados} con plazo renovado, {omitidos} omitidos.")
    else:
        print("Simulacro: no se escribió nada. Repite con --aplicar.")


if __name__ == "__main__":
    main()
