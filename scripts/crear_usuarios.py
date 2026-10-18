"""Crea un CLIENTE, un ASESOR y un operador (BACKOFFICE). Solo biblioteca estándar.

Todo pasa por el BFF, como lo haría el frontend, con una excepción: el primer BACKOFFICE. El BFF
exige un BACKOFFICE para crear usuarios, así que mientras no exista ninguno hay que crearlo por
otro lado. Para eso sirve `--auth URL`: llama a `POST /usuarios` de AUTH, que no pide sesión (en
Cloud Run lo protege IAM, `roles/run.invoker`, con tu token de identidad de gcloud) y crea SOLO al
operador. Con ese operador se crea el resto por el BFF.

Las credenciales nunca viajan como argumentos (quedan en el historial de la terminal):
  BFF_URL            URL base del BFF (por defecto http://localhost:8000)
  USUARIO_BACKOFFICE alias del BACKOFFICE que crea a los demás, por el BFF (si falta, pregunta)
  CLAVE_BACKOFFICE   su clave (si falta, la pregunta sin mostrarla)
  TOKEN_IDENTIDAD    token de identidad para AUTH en https (si falta, lo pide a gcloud)
  CLAVE_CLIENTE, CLAVE_ASESOR, CLAVE_OPERADOR
                     clave de cada usuario nuevo; si falta, se genera una al azar

Uso en staging, una vez: el primer BACKOFFICE (su clave va a Secret Manager, no a la pantalla):
    python scripts/crear_usuarios.py --auth https://<url-de-auth> --vault solventa-511815

Después, el CLIENTE y el ASESOR por el BFF, con ese operador:
    export USUARIO_BACKOFFICE=operador.demo
    export CLAVE_BACKOFFICE=$(gcloud secrets versions access latest --secret usuario-operador-demo)
    python scripts/crear_usuarios.py --bff https://<url-del-bff> --vault solventa-511815

Uso en local (con `docker compose up -d --wait`; el BACKOFFICE local es público, no es secreto):
    export USUARIO_BACKOFFICE=admin.solventa CLAVE_BACKOFFICE=admin-local-solventa
    python scripts/crear_usuarios.py

Es idempotente: un alias que ya existe se deja como está (no se toca su clave) y no es un error.
Por el BFF, el operador también se intenta crear (si ya existe, se salta)."""

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


class SinConexion(Fallo):
    """El destino no responde: no tiene sentido seguir con el resto de los usuarios."""


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
        raise SinConexion(f"no pude hablar con el BFF en {bff}: {error}") from error
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


def token_de_identidad(auth: str) -> str | None:
    """Token para el `roles/run.invoker` de Cloud Run; en http (local) no hace falta."""
    if not auth.startswith("https://"):
        return None
    token = os.getenv("TOKEN_IDENTIDAD")
    if token:
        return token
    gcloud = shutil.which("gcloud")
    if gcloud is None:
        raise Fallo("AUTH está en https y no encuentro `gcloud` ni TOKEN_IDENTIDAD.")
    salida = subprocess.run(
        [gcloud, "auth", "print-identity-token"], capture_output=True, text=True
    )
    if salida.returncode != 0:
        raise Fallo(f"no pude pedir el token de identidad a gcloud: {salida.stderr.strip()}")
    return salida.stdout.strip()


def crear_usuario_en_auth(auth: str, token: str | None, alias: str, clave: str, rol: str, nombre):
    """Igual que `crear_usuario`, pero llamando a `POST /usuarios` de AUTH."""
    entrada = {"usuario": alias, "clave": clave, "rol": rol}
    if nombre:
        entrada["nombre"] = nombre
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(
        f"{auth}/usuarios", data=json.dumps(entrada).encode(), headers=cabeceras
    )
    try:
        with urllib.request.urlopen(peticion, timeout=60):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 409:
            return False
        if error.code in (401, 403):
            raise Fallo(
                "AUTH rechazó tu identidad: tu cuenta necesita `roles/run.invoker` en `auth`."
            ) from error
        detalle = json.loads(error.read() or b"{}").get("detail", error.reason)
        raise Fallo(f"no pude crear a {alias} ({rol}): {detalle}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise SinConexion(f"no pude hablar con AUTH en {auth}: {error}") from error


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
    parser.add_argument(
        "--auth",
        metavar="URL",
        help="solo el primer BACKOFFICE: lo crea directo en AUTH, sin BFF; ignora --bff",
    )
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

    try:
        if opciones.auth:
            destino = opciones.auth.rstrip("/")
            token = token_de_identidad(destino)
            crear = crear_usuario_en_auth
            print(f"AUTH (directo): {destino}")
        else:
            destino = opciones.bff.rstrip("/")
            admin = os.getenv("USUARIO_BACKOFFICE") or input("Usuario BACKOFFICE: ").strip()
            clave_admin = os.getenv("CLAVE_BACKOFFICE") or getpass.getpass("Clave del BACKOFFICE: ")
            token = iniciar_sesion(destino, admin, clave_admin)
            crear = crear_usuario
            print(f"BFF: {destino}")
    except Fallo as error:
        print(f"FALLO: {error}", file=sys.stderr)
        return 1

    # (alias, rol, nombre, variable con la clave); solo el CLIENTE lleva nombre.
    pedidos = [
        (opciones.cliente, "CLIENTE", opciones.nombre_cliente, "CLAVE_CLIENTE"),
        (opciones.asesor, "ASESOR", None, "CLAVE_ASESOR"),
        (opciones.operador, "BACKOFFICE", None, "CLAVE_OPERADOR"),
    ]
    if opciones.auth:
        # Saltarse el BFF es solo para arrancar: el resto se crea por el BFF, con su control de rol.
        pedidos = pedidos[2:]
        print("  (solo el primer BACKOFFICE; el CLIENTE y el ASESOR se crean por el BFF)")
    errores = 0
    for alias, rol, nombre, variable in pedidos:
        propia = os.getenv(variable)
        clave = propia or generar_clave()
        try:
            creado = crear(destino, token, alias, clave, rol, nombre)
        except SinConexion as error:
            print(f"FALLO: {error}", file=sys.stderr)
            return 1
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
