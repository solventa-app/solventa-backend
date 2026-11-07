"""Asigna un medio de pago a los cobros de staging que no lo tienen (conciliación de pagos, HU-W05).

Por qué existe: la conciliación muestra la marca y el tipo del medio de cada cobro (`medio_pago_id`
→ `medios_pago`). Solo dos caminos guardan el medio: la página de pago (cobro `INTERACTIVO`, W03) y
el cobro recurrente programado. Los cobros creados con la mutación `crearCobro` / `POST /cobros`
(el cobro a mano de docs/guia-demo-staging.md 2.3, `scripts/sembrar_cobros.py` y las pruebas E2E de
`scripts/probar_cobros.py`, pólizas `POL-E2E-*` y `DIAG-*`) quedan con origen `RECURRENTE` y sin
medio, y la pantalla los muestra vacíos.

Qué hace: agrupa los cobros sin medio por (póliza, cliente) y crea **un** medio por grupo, como lo
haría la página de pago:
- `token_cifrado`: el token del propio cobro, descifrado (dato asociado: id del cobro) y vuelto a
  cifrar con el id del medio, con la llave `clave-cifrado` de Secret Manager (la de PAYMENTS).
- `marca` y `tipo`: rotan entre VISA, MASTERCARD y AMEX, crédito y débito (el cobro no guarda la
  marca; es dato de demostración).
- `propuesta_id`: la de la póliza en POLICY; si la póliza no existe allí (las de prueba), la póliza.
Después fija `medio_pago_id` en esos cobros. No toca ningún cobro que ya tenga medio. Todo en una
transacción: si algo falla, no cambia nada.

Sin --aplicar es un simulacro. Requisitos: gcloud con acceso a solventa-511815 y, en un entorno
aparte: `pip install "cloud-sql-python-connector[pg8000]" cryptography`.

Uso (docs/guia-demo-staging.md, sección 9):
    .venv-ops/Scripts/python scripts/asignar_medios_cobros_staging.py             # simulacro
    .venv-ops/Scripts/python scripts/asignar_medios_cobros_staging.py --aplicar
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

RAIZ = Path(__file__).resolve().parents[1]
PROYECTO = "solventa-511815"
INSTANCIA = f"{PROYECTO}:us-central1:solventa-backend-postgres"
MEDIOS = [
    ("VISA", "CREDITO"),
    ("MASTERCARD", "DEBITO"),
    ("AMEX", "CREDITO"),
    ("VISA", "DEBITO"),
    ("MASTERCARD", "CREDITO"),
]


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
    parser.add_argument(
        "--aplicar", action="store_true", help="escribe de verdad (sin esto es un simulacro)"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(RAIZ / "services" / "payments"))
    from google.cloud.sql.connector import Connector

    from app.application.cifrado import CifradorAesGcm

    cifrador = CifradorAesGcm.desde_base64(secreto("clave-cifrado"))
    conector = Connector()

    def conectar(base: str):
        url = urlparse(secreto(f"database-url-{base}"))
        return conector.connect(
            INSTANCIA,
            "pg8000",
            user=unquote(url.username),
            password=unquote(url.password),
            db=base,
            ip_type="public",
        )

    payments = conectar("payments")
    cur = payments.cursor()
    cur.execute(
        "SELECT id, poliza_id, cliente_id, token_medio_pago FROM cobros "
        "WHERE medio_pago_id IS NULL ORDER BY creado_en"
    )
    grupos: dict[tuple, list] = defaultdict(list)
    for cobro_id, poliza_id, cliente_id, token in cur.fetchall():
        grupos[(poliza_id, cliente_id)].append((cobro_id, token))
    if not grupos:
        print("Todos los cobros ya tienen medio de pago.")
        return

    policy = conectar("policy")
    pc = policy.cursor()
    pc.execute("SELECT id, propuesta_id FROM polizas WHERE id = ANY(%s)", ([p for p, _ in grupos],))
    propuestas = dict(pc.fetchall())
    policy.close()

    total = 0
    for i, ((poliza_id, cliente_id), cobros) in enumerate(grupos.items()):
        marca, tipo = MEDIOS[i % len(MEDIOS)]
        medio_id = str(uuid.uuid4())
        # El token del primer cobro del grupo; si no se puede descifrar, uno de demostración.
        try:
            token = cifrador.descifrar(cobros[0][1], cobros[0][0])
        except Exception:  # noqa: BLE001 - dato viejo o de prueba: no detiene el parche
            token = "tok_demo_conciliacion"
        print(f"póliza {poliza_id[:24]:<24} {len(cobros):>3} cobros -> {marca} {tipo}")
        total += len(cobros)
        if not args.aplicar:
            continue
        cur.execute(
            "INSERT INTO medios_pago (id, propuesta_id, cliente_id, token_cifrado, marca, tipo) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                medio_id,
                propuestas.get(poliza_id) or poliza_id,
                cliente_id,
                cifrador.cifrar(token, medio_id),
                marca,
                tipo,
            ),
        )
        cur.execute(
            "UPDATE cobros SET medio_pago_id = %s, actualizado_en = now() "
            "WHERE id = ANY(%s) AND medio_pago_id IS NULL",
            (medio_id, [c for c, _ in cobros]),
        )
    if args.aplicar:
        payments.commit()
        cur.execute("SELECT count(*) FROM cobros WHERE medio_pago_id IS NULL")
        print(
            f"Listo: {len(grupos)} medios creados, {total} cobros con medio. "
            f"Sin medio quedan: {cur.fetchone()[0]}"
        )
    else:
        print(f"Simulacro: {len(grupos)} medios para {total} cobros. Repite con --aplicar.")
    payments.close()
    conector.close()


if __name__ == "__main__":
    main()
