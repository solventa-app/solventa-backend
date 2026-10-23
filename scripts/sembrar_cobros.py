"""Cobros de ejemplo para la demo LOCAL de HU-W05 (pantalla W05-01, Conciliación de pagos).

Crea, por el BFF y con el BACKOFFICE local, los 5 clientes del prototipo (Figma W05-01) y un
cobro para cada uno, usando el modo del stub de la pasarela para dejar cada estado:

- COBRADO: María Gutiérrez y Sandra Ospina (pasarela sana).
- RECHAZADO: Luisa Fernanda Parra (pasarela en `rechaza`): se puede reintentar hasta 3 veces.
- PENDIENTE: Carlos Rincón y Jorge Mendoza (pasarela en `error`). El reintento automático de
  PAYMENTS los vuelve a intentar a los pocos segundos y, con la pasarela sana, quedan COBRADO:
  así se ve el reintento automático en la demo.

Es idempotente: los alias y los `idIntentoCobro` son fijos, así que correrlo dos veces no
duplica nada (los usuarios se reutilizan y los cobros se devuelven como estaban). Para sembrar
cobros nuevos (p. ej. repetir la demo del reintento automático) pasa `SUFIJO=<texto>`: se agrega
a la póliza y al `idIntentoCobro`, los clientes se reutilizan y cada sufijo distinto crea cinco
cobros nuevos (el mismo sufijo, otra vez, no duplica).

Necesita el stub de la pasarela (`STUB_PASARELA_URL`) para poner cada cobro en su estado. Datos
ficticios. En staging el stub es privado: pasa `TOKEN_PASARELA` (o deja que lo pida a `gcloud`) y
apunta `BFF_URL` a la URL del gateway, con `USUARIO_BACKOFFICE` y `CLAVE_BACKOFFICE` de un operador
real de staging (sus claves quedan en Secret Manager, ver `scripts/crear_usuarios.py`).

Uso (con `docker compose --profile servicios up -d`):
    python3 scripts/sembrar_cobros.py
    SUFIJO=ronda2 python3 scripts/sembrar_cobros.py   # cinco cobros nuevos
"""

import json
import os
import subprocess
import sys
import urllib.request

BFF = os.getenv("BFF_URL", "http://localhost:8000") + "/graphql/web"
STUB_PASARELA = os.getenv("STUB_PASARELA_URL", "http://localhost:8103")
USUARIO_BACKOFFICE = os.getenv("USUARIO_BACKOFFICE", "admin.solventa")
CLAVE_BACKOFFICE = os.getenv("CLAVE_BACKOFFICE", "admin-local-solventa")
CLAVE_CLIENTES = "clave-demo-cliente"
SUFIJO = os.getenv("SUFIJO", "").strip()

# (alias, nombre, póliza, prima mensual, modo de la pasarela) — como el prototipo W05-01.
# El orden importa: los PENDIENTE van al final para que el reintento automático de PAYMENTS no
# los encuentre con la pasarela en `rechaza` (se quedarían rechazados).
COBROS = [
    ("demo.maria", "María Gutiérrez", "VH-2026-0048217", "185000.00", "sano"),
    ("demo.sandra", "Sandra Ospina", "VH-2026-0048077", "198500.00", "sano"),
    ("demo.luisa", "Luisa Fernanda Parra", "VH-2026-0048155", "164800.00", "rechaza"),
    ("demo.carlos", "Carlos Rincón", "VH-2026-0048190", "212400.00", "error"),
    ("demo.jorge", "Jorge Mendoza", "VH-2026-0048101", "240000.00", "error"),
]


def _http(url: str, cuerpo: dict | None = None, token: str | None = None) -> dict:
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    with urllib.request.urlopen(
        urllib.request.Request(url, data=datos, headers=cabeceras), timeout=10
    ) as respuesta:
        return json.loads(respuesta.read())


def _graphql(query: str, variables: dict, token: str | None = None) -> dict:
    cuerpo = _http(BFF, {"query": query, "variables": variables}, token)
    if cuerpo.get("errors"):
        raise SystemExit(f"Error del BFF: {cuerpo['errors'][0]['message']}")
    return cuerpo["data"]


def _token_pasarela() -> str | None:
    """El stub de staging es privado (Cloud Run): pide un token de identidad. Local: ninguno."""
    if os.getenv("TOKEN_PASARELA"):
        return os.environ["TOKEN_PASARELA"]
    if not STUB_PASARELA.startswith("https://"):
        return None
    try:
        return subprocess.run(
            ["gcloud", "auth", "print-identity-token", "--audiences=stub-pasarela"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise SystemExit(
            "El stub de la pasarela es privado: define TOKEN_PASARELA "
            "(gcloud auth print-identity-token --audiences=stub-pasarela)."
        ) from None


def _modo_pasarela(modo: str) -> None:
    _http(f"{STUB_PASARELA}/control/modo", {"modo": modo}, _token_pasarela())


def _cliente(alias: str, nombre: str, token: str, existentes: dict[str, str]) -> str:
    if alias in existentes:
        return existentes[alias]
    creado = _graphql(
        """mutation($e: CrearUsuarioInput!) { crearUsuario(entrada: $e) {
          __typename ... on Usuario { id } ... on ErrorUsuario { mensaje } } }""",
        {"e": {"usuario": alias, "clave": CLAVE_CLIENTES, "rol": "CLIENTE", "nombre": nombre}},
        token,
    )["crearUsuario"]
    if creado["__typename"] != "Usuario":
        raise SystemExit(f"No se pudo crear {alias}: {creado['mensaje']}")
    return creado["id"]


def main() -> int:
    sesion = _graphql(
        """mutation($e: CredencialesInput!) { iniciarSesion(entrada: $e) {
          __typename ... on Sesion { token } ... on ErrorAutenticacion { mensaje } } }""",
        {"e": {"usuario": USUARIO_BACKOFFICE, "clave": CLAVE_BACKOFFICE}},
    )["iniciarSesion"]
    if sesion["__typename"] != "Sesion":
        raise SystemExit(f"No se pudo iniciar sesión como BACKOFFICE: {sesion['mensaje']}")
    token = sesion["token"]
    existentes = {
        u["usuario"]: u["id"]
        for u in _graphql("query { usuarios(rol: CLIENTE) { id usuario } }", {}, token)["usuarios"]
    }

    try:
        for alias, nombre, poliza, prima, modo in COBROS:
            if SUFIJO:
                poliza = f"{poliza}-{SUFIJO}"
            cliente_id = _cliente(alias, nombre, token, existentes)
            _modo_pasarela(modo)
            cobro = _graphql(
                """mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) {
                  __typename ... on Cobro { estado } ... on ErrorCobro { mensaje } } }""",
                {
                    "e": {
                        "idIntentoCobro": f"semilla-w05-{poliza}",
                        "polizaId": poliza,
                        "clienteId": cliente_id,
                        "monto": {"valor": prima, "moneda": "COP"},
                        "tokenMedioPago": "tok_demo_semilla",
                    }
                },
                token,
            )["crearCobro"]
            print(f"{poliza}  {nombre:<22} {cobro.get('estado') or cobro.get('mensaje')}")
    finally:
        _modo_pasarela("sano")

    print(f"Listo. Entra como {USUARIO_BACKOFFICE} a Conciliación de pagos (W05-01).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
