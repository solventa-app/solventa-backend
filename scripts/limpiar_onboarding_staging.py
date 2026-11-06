"""Borra cuentas del onboarding móvil (HU-M01) en staging para repetir una demo con la misma cédula.

Por qué existe: una cédula verificada queda amarrada a su cuenta en dos lugares, el índice único
`usuarios_titular_unico` de AUTH y `ux_sesiones_titular_verificado` de ONBOARDING (sesión
VERIFICADA). Si alguien crea otra cuenta y verifica la misma cédula, el rechazo
`DOCUMENTO_YA_REGISTRADO` viene de nuestro sistema, no de Didit. Para repetir la demo hay que
borrar la cuenta anterior.

Qué borra:
  AUTH        usuarios CLIENTE del onboarding (los que tienen `correo_hmac` o salen de una sesión) y
              sus `refresh_tokens`.
  ONBOARDING  las sesiones y lo que cuelga de ellas: `datos_extraidos`, `verificaciones` y la
              bitácora `eventos_onboarding`. Esa bitácora es append-only por trigger. El script
              desactiva el trigger, borra y lo vuelve a activar en la misma transacción: si algo
              falla, no cambia nada y el trigger sigue activo.

Qué no toca: los consentimientos de AUTH (append-only, no bloquean el registro), los archivos de
almacenamiento (se borran solos a los 30 días), los perfiles de RISK (se reemplazan con el próximo
cálculo) ni a ASESOR, BACKOFFICE o socios. **Se niega a borrar** un cliente cuyo titular tenga
pólizas, propuestas o cobros: esos datos son de otras historias (W03, W04, W05).

Dos modos:
  --cedula   solo la cuenta de esa cédula. El número se pide sin mostrarlo (o va en la variable
             CEDULA); nunca como argumento, que queda en el historial. Se busca por su HMAC.
  --todas    todas las cuentas del onboarding móvil y todas las sesiones.

Sin --aplicar es un simulacro: cuenta lo que borraría y escribe el respaldo, pero no borra nada.
El respaldo (JSON con los campos personales cifrados) queda en --respaldo (por defecto el directorio
actual). No lo subas al repo y bórralo cuando la demo salga bien.

Requisitos: gcloud con la cuenta que tiene acceso a solventa-511815 (ADC: `gcloud auth
application-default login`) y, en un entorno aparte:
    python -m venv .venv-ops
    .venv-ops/Scripts/pip install "cloud-sql-python-connector[pg8000]" pymongo

Uso (guía completa en docs/guia-demo-staging.md, sección 7):
    .venv-ops/Scripts/python scripts/limpiar_onboarding_staging.py --cedula            # simulacro
    .venv-ops/Scripts/python scripts/limpiar_onboarding_staging.py --cedula --aplicar
    .venv-ops/Scripts/python scripts/limpiar_onboarding_staging.py --todas --aplicar
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

PROYECTO = "solventa-511815"
INSTANCIA = f"{PROYECTO}:us-central1:solventa-backend-postgres"
TABLAS_SESION = ("datos_extraidos", "verificaciones", "eventos_onboarding")
TRIGGER = "trg_eventos_onboarding_append_only"


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


def conectar(conector, base: str):
    url = urlparse(secreto(f"database-url-{base}"))
    return conector.connect(
        INSTANCIA,
        "pg8000",
        user=unquote(url.username),
        password=unquote(url.password),
        db=base,
        ip_type="public",
    )


def filas(cursor, sql: str, params: tuple = ()) -> list[dict]:
    cursor.execute(sql, params)
    columnas = [d[0] for d in cursor.description]
    return [dict(zip(columnas, fila, strict=True)) for fila in cursor.fetchall()]


def titular_de_cedula() -> str:
    cedula = (os.environ.get("CEDULA") or getpass.getpass("Cédula (no se muestra): ")).strip()
    if not cedula:
        sys.exit("Falta la cédula.")
    # Mismo HMAC que AUTH, ONBOARDING y el BFF (`titular_id_desde_documento`).
    return hmac.new(
        secreto("clave-cliente-id").encode(), cedula.encode(), hashlib.sha256
    ).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument(
        "--cedula", action="store_true", help="solo la cuenta de una cédula (se pide sin mostrarla)"
    )
    modo.add_argument(
        "--todas", action="store_true", help="todas las cuentas y sesiones del onboarding móvil"
    )
    parser.add_argument(
        "--aplicar", action="store_true", help="borra de verdad (sin esto es un simulacro)"
    )
    parser.add_argument(
        "--respaldo", default=".", help="directorio del respaldo JSON (por defecto el actual)"
    )
    args = parser.parse_args()

    import pymongo
    from google.cloud.sql.connector import Connector  # dependencias de operación, no del repo

    titular = titular_de_cedula() if args.cedula else None
    conector = Connector()
    onboarding, auth = conectar(conector, "onboarding"), conectar(conector, "auth")
    co, ca = onboarding.cursor(), auth.cursor()

    if titular:
        usuarios = filas(
            ca, "select * from usuarios where rol = 'CLIENTE' and titular_id = %s", (titular,)
        )
        sesiones = filas(
            co,
            "select * from sesiones_onboarding where titular_id = %s or usuario_id = any(%s)",
            (titular, [u["id"] for u in usuarios]),
        )
    else:
        sesiones = filas(co, "select * from sesiones_onboarding")
        usuarios = filas(
            ca,
            "select * from usuarios where rol = 'CLIENTE' "
            "and (correo_hmac is not null or id = any(%s))",
            ([s["usuario_id"] for s in sesiones if s["usuario_id"]],),
        )
    ids_sesion = [s["id"] for s in sesiones]
    ids_usuario = [u["id"] for u in usuarios]
    titulares = sorted({x["titular_id"] for x in usuarios + sesiones if x.get("titular_id")})

    respaldo: dict[str, list[dict]] = {
        "onboarding.sesiones_onboarding": sesiones,
        "auth.usuarios": usuarios,
    }
    for tabla in TABLAS_SESION:
        respaldo[f"onboarding.{tabla}"] = filas(
            co, f"select * from {tabla} where sesion_id = any(%s)", (ids_sesion,)
        )
    respaldo["auth.refresh_tokens"] = filas(
        ca, "select * from refresh_tokens where usuario_id = any(%s)", (ids_usuario,)
    )

    # En POLICY, PAYMENTS y RISK, `cliente_id` es el titular (HMAC del documento), no el id del
    # usuario.
    negocio: dict[str, int] = {}
    for base, tabla in (("policy", "polizas"), ("policy", "propuestas"), ("payments", "cobros")):
        conexion = conectar(conector, base)
        cursor = conexion.cursor()
        cursor.execute(f"select count(*) from {tabla} where cliente_id = any(%s)", (titulares,))
        negocio[f"{base}.{tabla}"] = cursor.fetchone()[0]
        conexion.close()
    mongo = pymongo.MongoClient(secreto("mongo-uri-risk"), serverSelectionTimeoutMS=15000)
    perfiles = mongo.get_default_database()["perfiles"].count_documents(
        {"cliente_id": {"$in": titulares}}
    )
    mongo.close()

    print(
        f"Sesiones de onboarding: {len(sesiones)} "
        f"({', '.join(f'{t}: {len(respaldo[f"onboarding.{t}"])}' for t in TABLAS_SESION)})"
    )
    estados = {u["estado"] for u in usuarios}
    print(
        f"Usuarios CLIENTE: {len(usuarios)} por estado "
        f"{ {e: sum(u['estado'] == e for u in usuarios) for e in estados} } "
        f"| refresh_tokens: {len(respaldo['auth.refresh_tokens'])}"
    )
    print(
        f"Datos de negocio de esos titulares: {negocio} | risk.perfiles (no se tocan): {perfiles}"
    )

    if not sesiones and not usuarios:
        print("No hay nada que borrar.")
        return
    if any(negocio.values()):
        sys.exit(
            "Alto: hay pólizas, propuestas o cobros de esos titulares. "
            "No borro clientes con datos de negocio."
        )

    sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    ruta = Path(args.respaldo) / f"respaldo-onboarding-{sello}.json"
    ruta.write_text(json.dumps(respaldo, default=str), encoding="utf-8")
    print(f"Respaldo: {ruta.resolve()}")

    if not args.aplicar:
        print("Simulacro: no se borró nada. Repite con --aplicar.")
        return

    ca.execute("delete from refresh_tokens where usuario_id = any(%s)", (ids_usuario,))
    ca.execute("delete from usuarios where id = any(%s)", (ids_usuario,))
    print(f"AUTH: {ca.rowcount} usuarios borrados.")
    auth.commit()

    co.execute(f"ALTER TABLE eventos_onboarding DISABLE TRIGGER {TRIGGER}")
    for tabla in TABLAS_SESION:
        co.execute(f"delete from {tabla} where sesion_id = any(%s)", (ids_sesion,))
    co.execute("delete from sesiones_onboarding where id = any(%s)", (ids_sesion,))
    print(f"ONBOARDING: {co.rowcount} sesiones borradas.")
    co.execute(f"ALTER TABLE eventos_onboarding ENABLE TRIGGER {TRIGGER}")
    onboarding.commit()
    co.execute("select tgenabled from pg_trigger where tgname = %s", (TRIGGER,))
    activo = co.fetchone()[0] == "O"
    print(f"Trigger de la bitácora: {'activo' if activo else 'REVISAR, no quedó activo'}")

    onboarding.close()
    auth.close()
    conector.close()


if __name__ == "__main__":
    main()
