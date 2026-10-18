"""Crea un CLIENTE, un ASESOR y un operador (BACKOFFICE) por el BFF, como lo haría el frontend.

La API exige un BACKOFFICE para crear usuarios, así que el script inicia sesión con uno que ya
exista (el de docker compose en local; en staging, el primero se crea con el Cloud Run Job de
`infra/README.md`, «Primer usuario BACKOFFICE»). Solo biblioteca estándar.

Las credenciales nunca viajan como argumentos (quedan en el historial de la terminal):
  BFF_URL            URL base del BFF (por defecto http://localhost:8000)
  USUARIO_BACKOFFICE alias del BACKOFFICE que crea a los demás (si falta, lo pregunta)
  CLAVE_BACKOFFICE   su clave (si falta, la pregunta sin mostrarla)
  CLAVE_CLIENTE, CLAVE_ASESOR, CLAVE_OPERADOR
                     clave de cada usuario nuevo; si falta, se genera una al azar

Uso (local, con `docker compose up -d --wait`; el BACKOFFICE local es público, no es secreto):
    export USUARIO_BACKOFFICE=admin.solventa CLAVE_BACKOFFICE=admin-local-solventa
    python scripts/crear_usuarios.py

Uso (staging, guardando las claves generadas en Secret Manager en vez de mostrarlas):
    python scripts/crear_usuarios.py --bff https://<url-del-bff> --vault solventa-511815

Es idempotente: un alias que ya existe se deja como está (no se toca su clave) y no es un error."""

import argparse
import getpass
import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import urllib.error
import urllib.request

# Lo que AUTH responde cuando el alias ya existe (services/auth/app/usuarios.py).
YA_EXISTE = "Ya existe un usuario con ese alias."
LONGITUD_CLAVE = 16

INICIAR_SESION = """mutation($entrada: CredencialesInput!) { iniciarSesion(entrada: $entrada) {
  __typename ... on Sesion { token usuario { rol } } ... on ErrorAutenticacion { mensaje } } }"""
CREAR_USUARIO = """mutation($entrada: CrearUsuarioInput!) { crearUsuario(entrada: $entrada) {
  __typename ... on Usuario { id rol } ... on ErrorUsuario { mensaje } } }"""


class Fallo(Exception):
    """Algo que el usuario del script puede corregir; se muestra sin traza."""


def _graphql(bff: str, query: str, variables: dict, token: str | None = None) -> dict:
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(
        f"{bff}/graphql/web",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers=cabeceras,
    )
    try:
        # Un Cloud Run en frío puede tardar varios segundos en la primera petición.
        with urllib.request.urlopen(peticion, timeout=60) as respuesta:
            cuerpo = json.loads(respuesta.read())
    except (urllib.error.URLError, TimeoutError) as error:
        raise Fallo(f"no pude hablar con el BFF en {bff}: {error}") from error
    if cuerpo.get("errors"):
        raise Fallo(f"el BFF rechazó la operación: {cuerpo['errors'][0].get('message')}")
    return cuerpo["data"]


def iniciar_sesion(bff: str, usuario: str, clave: str) -> str:
    sesion = _graphql(bff, INICIAR_SESION, {"entrada": {"usuario": usuario, "clave": clave}})[
        "iniciarSesion"
    ]
    if sesion["__typename"] != "Sesion":
        raise Fallo(f"no pude iniciar sesión como {usuario}: {sesion['mensaje']}")
    if sesion["usuario"]["rol"] != "BACKOFFICE":
        raise Fallo(f"{usuario} no es BACKOFFICE; solo un BACKOFFICE puede crear usuarios.")
    return sesion["token"]


def crear_usuario(bff: str, token: str, alias: str, clave: str, rol: str, nombre: str | None):
    """`True` si lo creó, `False` si ya existía. Otro rechazo (alias inválido...) es un `Fallo`."""
    entrada = {"usuario": alias, "clave": clave, "rol": rol}
    if nombre:
        entrada["nombre"] = nombre
    resultado = _graphql(bff, CREAR_USUARIO, {"entrada": entrada}, token)["crearUsuario"]
    if resultado["__typename"] == "Usuario":
        return True
    if resultado["mensaje"] == YA_EXISTE:
        return False
    raise Fallo(f"no pude crear a {alias} ({rol}): {resultado['mensaje']}")


def generar_clave() -> str:
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(LONGITUD_CLAVE))


def nombre_secreto(alias: str) -> str:
    """Secret Manager no admite puntos en el nombre."""
    return "usuario-" + alias.replace(".", "-").replace("_", "-")


def guardar_en_vault(proyecto: str, alias: str, clave: str) -> str:
    """Guarda la clave en Secret Manager (por la entrada estándar, sin salto de línea final)."""
    gcloud = shutil.which("gcloud")
    if gcloud is None:
        raise Fallo("no encuentro `gcloud`; instálalo o ejecuta sin --vault.")
    secreto = nombre_secreto(alias)

    def secrets_gcloud(*argumentos: str, entrada: str | None = None):
        return subprocess.run(
            [gcloud, "secrets", *argumentos, f"--project={proyecto}"],
            input=entrada,
            capture_output=True,
            text=True,
        )

    # Si el secreto ya existe, `create` falla y solo se agrega una versión.
    secrets_gcloud("create", secreto, "--replication-policy=automatic")
    agregado = secrets_gcloud("versions", "add", secreto, "--data-file=-", entrada=clave)
    if agregado.returncode != 0:
        raise Fallo(f"no pude guardar la clave en Secret Manager: {agregado.stderr.strip()}")
    return secreto


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crea un CLIENTE, un ASESOR y un operador.")
    parser.add_argument("--bff", default=os.getenv("BFF_URL", "http://localhost:8000"))
    parser.add_argument("--cliente", default="cliente.demo", help="alias del CLIENTE")
    parser.add_argument("--asesor", default="asesor.demo", help="alias del ASESOR")
    parser.add_argument(
        "--operador", default="operador.demo", help="alias del operador (BACKOFFICE)"
    )
    parser.add_argument("--nombre-cliente", default="Cliente Demo", help="nombre del CLIENTE")
    parser.add_argument(
        "--vault",
        metavar="PROYECTO",
        help="guarda en Secret Manager las claves generadas en vez de mostrarlas",
    )
    opciones = parser.parse_args(argumentos)
    bff = opciones.bff.rstrip("/")

    admin = os.getenv("USUARIO_BACKOFFICE") or input("Usuario BACKOFFICE: ").strip()
    clave_admin = os.getenv("CLAVE_BACKOFFICE") or getpass.getpass("Clave del BACKOFFICE: ")
    try:
        token = iniciar_sesion(bff, admin, clave_admin)
    except Fallo as error:
        print(f"FALLO: {error}", file=sys.stderr)
        return 1

    # (alias, rol, nombre, variable con la clave); solo el CLIENTE lleva nombre.
    pedidos = [
        (opciones.cliente, "CLIENTE", opciones.nombre_cliente, "CLAVE_CLIENTE"),
        (opciones.asesor, "ASESOR", None, "CLAVE_ASESOR"),
        (opciones.operador, "BACKOFFICE", None, "CLAVE_OPERADOR"),
    ]
    errores = 0
    print(f"BFF: {bff}")
    for alias, rol, nombre, variable in pedidos:
        propia = os.getenv(variable)
        clave = propia or generar_clave()
        try:
            creado = crear_usuario(bff, token, alias, clave, rol, nombre)
        except Fallo as error:
            errores += 1
            print(f"  {rol:<10} {alias}: FALLO {error}", file=sys.stderr)
            continue
        if not creado:
            print(f"  {rol:<10} {alias}: ya existía (no se toca su clave)")
        elif propia:
            print(f"  {rol:<10} {alias}: creado, con la clave de {variable}")
        elif not opciones.vault:
            print(
                f"  {rol:<10} {alias}: creado, clave {clave}   (guárdala: no se vuelve a mostrar)"
            )
        else:
            try:
                secreto = guardar_en_vault(opciones.vault, alias, clave)
                print(f"  {rol:<10} {alias}: creado, clave en Secret Manager: {secreto}")
            except Fallo as error:
                # El usuario ya existe: sin la clave quedaría inservible, así que se muestra.
                errores += 1
                print(f"  {rol:<10} {alias}: creado, pero {error}", file=sys.stderr)
                print(f"             clave (guárdala ya): {clave}", file=sys.stderr)
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
