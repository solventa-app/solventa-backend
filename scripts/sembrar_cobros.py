"""Cobros de ejemplo para la demo LOCAL de HU-W05 (pantalla W05-01, Conciliación de pagos).

Crea, por el BFF y con el BACKOFFICE local, los 5 clientes del prototipo (Figma W05-01), cada
uno con **su tarjeta de prueba tokenizada**, y un cobro para cada uno:

1. La tarjeta se tokeniza en el **mock de la pasarela** (`POST /v1/tokens`): el número solo viaja
   ahí, nunca al BFF ni a PAYMENTS (regla #6; solo tarjetas de prueba, jamás una real).
2. El token se registra en PAYMENTS (`POST /medios-pago`, interno): queda cifrado por campo y
   solo se muestran la marca y los últimos 4.
3. El cobro se crea por el BFF con `medioPagoId`, y el back-office ve "Visa ····4242".

Los estados se dejan así:

- COBRADO: María Gutiérrez y Sandra Ospina (pasarela sana).
- RECHAZADO: Luisa Fernanda Parra, con su **tarjeta sin fondos** (control del mock
  `POST /control/tokens/{token}/estado`): se puede reintentar hasta 3 veces, pero seguirá
  rechazada mientras su tarjeta no vuelva a `activa` (ver el aviso al final).
- PENDIENTE: Carlos Rincón y Jorge Mendoza (pasarela en `error`). El reintento automático de
  PAYMENTS los vuelve a intentar a los pocos segundos y, con la pasarela sana, quedan COBRADO:
  así se ve el reintento automático en la demo.

Es idempotente: los alias, las tarjetas (el mock da siempre el mismo token a la misma tarjeta
de prueba) y los `idIntentoCobro` son fijos, así que correrlo dos veces no duplica nada.

Solo para local: necesita el stub de la pasarela (`STUB_PASARELA_URL`) y PAYMENTS
(`PAYMENTS_URL`). Datos ficticios.

Uso (con `docker compose --profile servicios up -d`):
    python3 scripts/sembrar_cobros.py
"""

import json
import os
import sys
import urllib.request

BFF = os.getenv("BFF_URL", "http://localhost:8000") + "/graphql/web"
STUB_PASARELA = os.getenv("STUB_PASARELA_URL", "http://localhost:8103")
PAYMENTS = os.getenv("PAYMENTS_URL", "http://localhost:8004")
USUARIO_BACKOFFICE = os.getenv("USUARIO_BACKOFFICE", "admin.solventa")
CLAVE_BACKOFFICE = os.getenv("CLAVE_BACKOFFICE", "admin-local-solventa")
CLAVE_CLIENTES = "clave-demo-cliente"

# (alias, nombre, póliza, prima mensual, modo de la pasarela, tarjeta de PRUEBA, estado de ella)
# Tarjetas de prueba públicas de las pasarelas (válidas con Luhn); ninguna es de una persona.
# El orden importa: los PENDIENTE van al final para que el reintento automático de PAYMENTS no
# los encuentre con la pasarela en `rechaza` (se quedarían rechazados).
COBROS = [
    (
        "demo.maria",
        "María Gutiérrez",
        "VH-2026-0048217",
        "185000.00",
        "sano",
        "4242424242424242",
        "activa",
    ),
    (
        "demo.sandra",
        "Sandra Ospina",
        "VH-2026-0048077",
        "198500.00",
        "sano",
        "5555555555554444",
        "activa",
    ),
    (
        "demo.luisa",
        "Luisa Fernanda Parra",
        "VH-2026-0048155",
        "164800.00",
        "sano",
        "4000056655665556",
        "fondos_insuficientes",
    ),
    (
        "demo.carlos",
        "Carlos Rincón",
        "VH-2026-0048190",
        "212400.00",
        "error",
        "378282246310005",
        "activa",
    ),
    (
        "demo.jorge",
        "Jorge Mendoza",
        "VH-2026-0048101",
        "240000.00",
        "error",
        "4111111111111111",
        "activa",
    ),
]
VENCIMIENTO_ANIO = 2035


def _http(
    url: str, cuerpo: dict | None = None, token: str | None = None, metodo: str | None = None
) -> dict:
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    with urllib.request.urlopen(
        urllib.request.Request(url, data=datos, headers=cabeceras, method=metodo), timeout=10
    ) as respuesta:
        return json.loads(respuesta.read())


def _graphql(query: str, variables: dict, token: str | None = None) -> dict:
    cuerpo = _http(BFF, {"query": query, "variables": variables}, token)
    if cuerpo.get("errors"):
        raise SystemExit(f"Error del BFF: {cuerpo['errors'][0]['message']}")
    return cuerpo["data"]


def _modo_pasarela(modo: str) -> None:
    _http(f"{STUB_PASARELA}/control/modo", {"modo": modo})


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


def _medio_de_pago(cliente_id: str, numero: str, estado: str) -> str:
    """Tokeniza la tarjeta de prueba en el MOCK, la registra en PAYMENTS y fija su estado.
    Devuelve el id del medio de pago. El número no se guarda ni se imprime."""
    tarjeta = _http(
        f"{STUB_PASARELA}/v1/tokens",
        {
            "numero": numero,
            "vencimiento_mes": 12,
            "vencimiento_anio": VENCIMIENTO_ANIO,
            "cvv": "123",
        },
    )
    medio = _http(
        f"{PAYMENTS}/medios-pago",
        {
            "cliente_id": cliente_id,
            "token": tarjeta["token"],
            "marca": tarjeta["marca"],
            "ultimos4": tarjeta["ultimos4"],
            "vencimiento_mes": tarjeta["vencimiento_mes"],
            "vencimiento_anio": tarjeta["vencimiento_anio"],
        },
    )
    _http(f"{STUB_PASARELA}/control/tokens/{tarjeta['token']}/estado", {"estado": estado})
    return medio["id"]


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
        for alias, nombre, poliza, prima, modo, numero, estado_tarjeta in COBROS:
            cliente_id = _cliente(alias, nombre, token, existentes)
            # Sana para tokenizar (la anterior pudo quedar en `error`) y luego el modo del cobro.
            _modo_pasarela("sano")
            medio_pago_id = _medio_de_pago(cliente_id, numero, estado_tarjeta)
            _modo_pasarela(modo)
            cobro = _graphql(
                """mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) {
                  __typename ... on Cobro { estado medioPago { marca ultimos4 } }
                  ... on ErrorCobro { mensaje } } }""",
                {
                    "e": {
                        "idIntentoCobro": f"semilla-kan208-{poliza}",
                        "polizaId": poliza,
                        "clienteId": cliente_id,
                        "monto": {"valor": prima, "moneda": "COP"},
                        "medioPagoId": medio_pago_id,
                    }
                },
                token,
            )["crearCobro"]
            medio = cobro.get("medioPago") or {}
            tarjeta = f"{medio.get('marca', '?')} ····{medio.get('ultimos4', '?')}"
            resultado = cobro.get("estado") or cobro.get("mensaje")
            print(f"{poliza}  {nombre:<22} {resultado:<10} {tarjeta}")
    finally:
        _modo_pasarela("sano")

    print(f"Listo. Entra como {USUARIO_BACKOFFICE} a Conciliación de pagos (W05-01).")
    print(
        "Aviso: la tarjeta de Luisa está sin fondos, así que sus reintentos seguirán rechazados. "
        "Para mostrar un reintento exitoso, vuelve a activarla:\n"
        "  curl -s localhost:8103/control/tokens   # busca su token (Visa ····5556)\n"
        "  curl -s -X POST localhost:8103/control/tokens/<token>/estado "
        "-H 'Content-Type: application/json' -d '{\"estado\":\"activa\"}'"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
