"""Cobros de ejemplo para la demo LOCAL de HU-W05 (pantalla W05-01, Conciliación de pagos).

Crea, por el BFF y con el BACKOFFICE local, los 5 clientes del prototipo (Figma W05-01) y un
cobro para cada uno, usando el modo del stub de la pasarela para dejar cada estado:

- COBRADO: María Gutiérrez y Sandra Ospina (pasarela sana).
- RECHAZADO: Luisa Fernanda Parra (pasarela en `rechaza`): se puede reintentar hasta 3 veces.
- PENDIENTE que se queda: Carlos Rincón. La pasarela tiene programado fallar SOLO para su
  referencia (`/control/referencias` del stub), así que el sistema lo sigue reintentando y la tabla
  lo muestra en «Pendiente de reintento» mientras el resto de la pasarela funciona.
- PENDIENTE que se recupera: Jorge Mendoza (pasarela en `error` al crearlo). El reintento
  automático lo vuelve a intentar y, con la pasarela sana, queda COBRADO: se ve el reintento.
- COBRANDO (opcional, `--con-cobrando`, solo local): en la vida real un cobro está «Cobrando» un
  instante (mientras la pasarela responde). Para que la demo lo muestre, se inserta uno directo en
  la base local de PAYMENTS, como si el proceso se hubiera caído a mitad del cobro. A los 2 minutos
  (`COBRO_ATASCADO_MINUTOS`) el sistema lo recupera solo: lo concilia y lo cobra. Es la única fila
  que no pasa por PAYMENTS (regla 1): por eso es opcional y solo local.

Qué significa cada estado: COBRANDO = el cobro está en curso; PENDIENTE = la pasarela no respondió
y el sistema lo reintenta solo (sin gastar los reintentos manuales); RECHAZADO = la pasarela lo
rechazó (p. ej. fondos insuficientes) y el operador lo puede reintentar a mano hasta 3 veces.

Es idempotente: los alias y los `idIntentoCobro` son fijos, así que correrlo dos veces no
duplica nada (los usuarios se reutilizan y los cobros se devuelven como estaban).

Necesita el stub de la pasarela (`STUB_PASARELA_URL`) para poner cada cobro en su estado. Datos
ficticios. En staging el stub es privado: pasa `TOKEN_PASARELA` (o deja que lo pida a `gcloud`) y
apunta `BFF_URL` a la URL del gateway, con `USUARIO_BACKOFFICE` y `CLAVE_BACKOFFICE` de un operador
real de staging (sus claves quedan en Secret Manager, ver `scripts/crear_usuarios.py`).

Uso (con `docker compose --profile servicios up -d`):
    python3 scripts/sembrar_cobros.py                  # cobrado, rechazado y pendientes
    python3 scripts/sembrar_cobros.py --con-cobrando   # y uno «Cobrando» (solo local)
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

# (alias, nombre, póliza, prima mensual, modo de la pasarela) — como el prototipo W05-01.
# El orden importa: los PENDIENTE van al final para que el reintento automático de PAYMENTS no
# los encuentre con la pasarela en `rechaza` (se quedarían rechazados).
COBROS = [
    ("demo.maria", "María Gutiérrez", "VH-2026-0048217", "185000.00", "sano"),
    ("demo.sandra", "Sandra Ospina", "VH-2026-0048077", "198500.00", "sano"),
    ("demo.luisa", "Luisa Fernanda Parra", "VH-2026-0048155", "164800.00", "rechaza"),
    ("demo.carlos", "Carlos Rincón", "VH-2026-0048190", "212400.00", "fija-error"),
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


def _modo_referencia(referencia: str, modo: str) -> None:
    """Solo esa referencia queda con ese modo (el resto de la pasarela sigue en su modo global)."""
    _http(
        f"{STUB_PASARELA}/control/referencias",
        {"referencia": referencia, "modo": modo},
        _token_pasarela(),
    )


ANDRES = ("demo.andres", "Andrés Felipe Duarte", "VH-2026-0048233", "198000.00")


def _cobro_cobrando(cliente_id: str) -> None:
    """Inserta un cobro en `cobrando` en la base LOCAL de PAYMENTS (docker compose)."""
    _, _, poliza, prima = ANDRES
    sql = (
        "INSERT INTO cobros (id, id_intento_cobro, poliza_id, cliente_id, monto, moneda, "
        "token_medio_pago, estado) VALUES (gen_random_uuid()::text, "
        f"'semilla-w05-{poliza}', '{poliza}', '{cliente_id}', '{prima}', 'COP', "
        "'tok_demo_semilla', 'cobrando') ON CONFLICT (id_intento_cobro) DO NOTHING;"
    )
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "payments",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


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
            cliente_id = _cliente(alias, nombre, token, existentes)
            if modo == "fija-error":
                _modo_pasarela("sano")
                _modo_referencia(f"semilla-w05-{poliza}", "error")
            else:
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
        if "--con-cobrando" in sys.argv:
            if BFF.startswith("https://"):
                raise SystemExit("--con-cobrando es solo para la demo local (docker compose).")
            alias, nombre, poliza, _ = ANDRES
            _cobro_cobrando(_cliente(alias, nombre, token, existentes))
            print(f"{poliza}  {nombre:<22} COBRANDO (se recupera solo a los 2 minutos)")
    finally:
        _modo_pasarela("sano")

    print(f"Listo. Entra como {USUARIO_BACKOFFICE} a Conciliación de pagos (W05-01).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
