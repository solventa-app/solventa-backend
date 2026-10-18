"""Prueba de punta a punta de HU-W05 (KAN-28): cobrar, no cobrar dos veces y conciliar.

Recorre por GraphQL, como el back-office, lo que HU-W05 promete, con la pasarela simulada
en cada modo:
  1. cobro con la pasarela sana            -> COBRADO con la referencia de la pasarela
  2. la misma orden dos veces              -> un solo cobro; otra orden con la misma clave, error
  3. pasarela lenta (cobra, pero > 700 ms) -> PENDIENTE; el reintento CONCILIA y no cobra de nuevo
  4. pasarela caída (503)                  -> PENDIENTE sin cobro; al volver, se cobra una sola vez
  5. pasarela rechaza                      -> RECHAZADO; hasta 3 reintentos y otro medio de pago
  6. tabla del back-office y permisos      -> cada cobro con su estado; sin sesión, sin datos

Deja la pasarela otra vez en `sano` y se puede repetir: cada corrida usa una póliza y claves
nuevas. Solo biblioteca estándar. Cada cobro de la prueba queda en la base (datos ficticios,
póliza POL-E2E-...).

Local (con `docker compose --profile servicios up -d --build --wait`; credenciales SOLO locales):
    python scripts/probar_cobros.py

Staging (Cloud Run; PAYMENTS, el ACL y la pasarela son privados: usa tu token de identidad de
gcloud y lee la clave del BACKOFFICE `operador.demo` de Secret Manager, sin mostrarla):
    python scripts/probar_cobros.py --staging solventa-511815

Con `--evidencia RUTA` escribe el resultado en Markdown (para docs/evidencias/). Código de
salida: 0 si todo pasa, 1 si alguna comprobación falla, 2 si no se pudo preparar la prueba
(sin conexión, sesión o cliente)."""

import argparse
import json
import os
import random
import shutil
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

REGION = "us-central1"
CLAVE_NUEVO_CLIENTE = "clave-cobros-0001"  # solo se usa si hay que crear al CLIENTE de la prueba

CREAR = """mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) { __typename
  ... on Cobro { id estado motivo intentos idTransaccion reintentosRestantes cliente { id nombre } }
  ... on ErrorCobro { mensaje } } }"""
REINTENTAR = """mutation($id: ID!) { reintentarCobro(cobroId: $id) {
  id estado motivo intentos idTransaccion reintentosRestantes } }"""
TABLA = """query($p: ID, $s: EstadoCobro) { cobros(polizaId: $p, estado: $s) {
  id polizaId estado idTransaccion cliente { nombre } } }"""
INICIAR_SESION = """mutation($e: CredencialesInput!) { iniciarSesion(entrada: $e) {
  __typename ... on Sesion { token } ... on ErrorAutenticacion { mensaje } } }"""
CLIENTES = "query { usuarios(rol: CLIENTE) { id usuario } }"
CREAR_USUARIO = """mutation($e: CrearUsuarioInput!) { crearUsuario(entrada: $e) {
  __typename ... on Usuario { id } ... on ErrorUsuario { mensaje } } }"""


class Config:
    bff = os.getenv("BFF_URL", "http://localhost:8000")
    payments = os.getenv("PAYMENTS_URL", "http://localhost:8004")
    pasarela = os.getenv("STUB_PASARELA_URL", "http://localhost:8103")
    acl = os.getenv("ACL_URL", "http://localhost:8005")
    staging: str | None = None
    token_identidad: str | None = os.getenv("TOKEN_IDENTIDAD")


class Fallo(Exception):
    """No se pudo preparar o seguir la prueba (no es una comprobación fallida)."""


resultados: list[tuple[str, str, bool]] = []
escenario_actual = ""


def comprobar(descripcion: str, condicion: object, detalle: object = "") -> bool:
    ok = bool(condicion)
    resultados.append((escenario_actual, descripcion, ok))
    extra = f"  -> {detalle}" if detalle and not ok else ""
    print(f"   {'OK   ' if ok else 'FALLA'} {descripcion}{extra}")
    return ok


def escenario(titulo: str) -> None:
    global escenario_actual
    escenario_actual = titulo
    print(titulo)


def _identidad() -> str | None:
    """Token de identidad para los servicios privados de Cloud Run (solo en staging)."""
    if not Config.staging:
        return None
    if Config.token_identidad is None:
        gcloud = shutil.which("gcloud")
        if gcloud is None:
            raise Fallo(
                "los servicios de staging son privados y no encuentro `gcloud` ni TOKEN_IDENTIDAD."
            )
        salida = subprocess.run(
            [gcloud, "auth", "print-identity-token"], capture_output=True, text=True
        )
        if salida.returncode != 0 or not salida.stdout.strip():
            raise Fallo(f"no pude pedir el token de identidad a gcloud: {salida.stderr.strip()}")
        Config.token_identidad = salida.stdout.strip()
    return Config.token_identidad


def _peticion(url: str, cuerpo: dict | None = None, token: str | None = None, timeout: int = 40):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            estado, crudo = respuesta.status, respuesta.read()
    except urllib.error.HTTPError as error:
        estado, crudo = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise Fallo(f"sin conexión con {url}: {error}") from error
    try:
        return estado, json.loads(crudo) if crudo else None
    except ValueError:
        return estado, crudo.decode("utf-8", "replace")


def _privado(url: str, cuerpo: dict | None = None):
    """Llamada directa a PAYMENTS, al ACL o a la pasarela (con identidad en staging)."""
    return _peticion(url, cuerpo, _identidad())


def _gql(consulta: str, variables: dict | None = None, token: str | None = None):
    estado, cuerpo = _peticion(
        Config.bff + "/graphql/web", {"query": consulta, "variables": variables or {}}, token
    )
    if not isinstance(cuerpo, dict):
        raise Fallo(f"el BFF respondió HTTP {estado} sin JSON: {str(cuerpo)[:120]}")
    return cuerpo.get("data"), cuerpo.get("errors")


def _modo(modo: str) -> None:
    estado, _ = _privado(f"{Config.pasarela}/control/modo", {"modo": modo})
    if estado != 200:
        raise Fallo(f"no pude poner la pasarela en `{modo}` (HTTP {estado})")


def _pasarela_cobro(clave: str):
    return _privado(f"{Config.pasarela}/v1/cobros/{clave}")


def _crear(
    token: str, clave: str, poliza: str, cliente_id: str | None, monto: str = "90000.00"
) -> dict:
    entrada = {
        "idIntentoCobro": clave,
        "polizaId": poliza,
        "monto": {"valor": monto, "moneda": "COP"},
        "tokenMedioPago": "tok_e2e",
    }
    if cliente_id:
        entrada["clienteId"] = cliente_id
    datos, errores = _gql(CREAR, {"e": entrada}, token)
    if errores or not datos:
        raise Fallo(f"crearCobro devolvió errores de GraphQL: {errores}")
    return datos["crearCobro"]


def _reintentar(token: str, cobro_id: str):
    datos, errores = _gql(REINTENTAR, {"id": cobro_id}, token)
    return (datos or {}).get("reintentarCobro"), errores


def _clave(prefijo: str) -> str:
    return f"{prefijo}-" + "".join(random.choices(string.ascii_lowercase, k=8))


def _esperar(condicion, segundos: float = 60, pausa: float = 2) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(pausa)
    return False


def _circuito_pasarela_cerrado() -> bool:
    """El circuito de la pasarela cierra solo, por la sonda del ACL (no por una petición)."""
    estado, cuerpo = _privado(f"{Config.acl}/circuitos")
    return (
        estado != 200
        or not isinstance(cuerpo, dict)
        or cuerpo.get("pasarela", "closed") == "closed"
    )


def _esperar_circuito() -> None:
    if not _esperar(_circuito_pasarela_cerrado, segundos=90):
        print("   (aviso: el circuito de la pasarela sigue abierto; lo siguiente puede degradarse)")


def _cobrar_al_volver(token: str, cobro_id: str) -> dict | None:
    """Reintenta hasta que el cobro queda COBRADO (con el circuito abierto no gasta intento)."""
    ultimo: dict | None = None

    def intento() -> bool:
        nonlocal ultimo
        ultimo, _ = _reintentar(token, cobro_id)
        return bool(ultimo) and ultimo["estado"] == "COBRADO"

    _esperar(intento, segundos=90, pausa=3)
    return ultimo


def preparar(argumentos) -> tuple[str, str]:
    """Calienta los servicios (staging), inicia sesión como BACKOFFICE y deja listo al CLIENTE."""
    if Config.staging:
        print("0. calentando los servicios (arranque en frío de Cloud Run)")
        for nombre, url in (
            ("bff", Config.bff),
            ("payments", Config.payments),
            ("acl-worker", Config.acl),
            ("stub-pasarela", Config.pasarela),
        ):
            token = None if nombre == "bff" else _identidad()
            listo = _esperar(
                lambda u=url, t=token: _peticion(u + "/health", token=t)[0] == 200, 60, 3
            )
            print(f"   {nombre}: {'listo' if listo else 'NO RESPONDE'}")
    else:
        print("0. sesión de BACKOFFICE y cliente de la prueba")

    usuario = os.getenv("USUARIO_BACKOFFICE") or argumentos.usuario
    clave = os.getenv("CLAVE_BACKOFFICE") or argumentos.clave
    if clave is None and Config.staging:
        gcloud = shutil.which("gcloud")
        secreto = "usuario-" + usuario.replace(".", "-").replace("_", "-")
        salida = subprocess.run(
            [
                gcloud or "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                f"--secret={secreto}",
                f"--project={Config.staging}",
            ],
            capture_output=True,
            text=True,
        )
        if salida.returncode != 0:
            raise Fallo(
                f"no pude leer la clave de {usuario} del vault ({secreto}): {salida.stderr.strip()}"
            )
        clave = salida.stdout.strip()
    if not clave:
        raise Fallo("falta la clave del BACKOFFICE (CLAVE_BACKOFFICE o --clave).")
    datos, errores = _gql(INICIAR_SESION, {"e": {"usuario": usuario, "clave": clave}})
    sesion = (datos or {}).get("iniciarSesion") or {}
    if sesion.get("__typename") != "Sesion":
        raise Fallo(f"no pude iniciar sesión como {usuario}: {sesion or errores}")
    token = sesion["token"]
    print(f"   -> sesión de {usuario}")

    datos, errores = _gql(CLIENTES, token=token)
    if errores:
        raise Fallo(f"{usuario} no puede listar clientes (¿no es BACKOFFICE?): {errores}")
    existente = next((c for c in datos["usuarios"] if c["usuario"] == argumentos.cliente), None)
    if existente is None:
        creado = _gql(
            CREAR_USUARIO,
            {
                "e": {
                    "usuario": argumentos.cliente,
                    "clave": CLAVE_NUEVO_CLIENTE,
                    "rol": "CLIENTE",
                    "nombre": argumentos.nombre_cliente,
                }
            },
            token,
        )[0]["crearUsuario"]
        if creado["__typename"] != "Usuario":
            raise Fallo(f"no pude crear al cliente {argumentos.cliente}: {creado}")
        existente = creado
        print(f"   -> cliente {argumentos.cliente} creado")
    else:
        print(f"   -> cliente {argumentos.cliente} existente")
    return token, existente["id"]


def cobro_sano(token: str, poliza: str, cliente_id: str, estado: dict) -> None:
    escenario("1. Cobro con la pasarela sana")
    clave = _clave("e2e-sano")
    cobro = _crear(token, clave, poliza, cliente_id)
    estado["sano"] = cobro
    comprobar("el cobro queda COBRADO", cobro.get("estado") == "COBRADO", cobro)
    comprobar(
        "trae la referencia de la pasarela (pg-…)",
        str(cobro.get("idTransaccion")).startswith("pg-"),
        cobro,
    )
    comprobar(
        "el back-office ve el nombre del cliente",
        bool((cobro.get("cliente") or {}).get("nombre")),
        cobro,
    )
    _, en_payments = _privado(f"{Config.payments}/cobros/{cobro.get('id')}")
    comprobar(
        "PAYMENTS guardó el mismo cobro con la misma referencia",
        isinstance(en_payments, dict)
        and en_payments.get("referencia") == cobro.get("idTransaccion"),
        en_payments,
    )
    estado_pasarela, en_pasarela = _pasarela_cobro(clave)
    comprobar(
        "la pasarela tiene ese cobro con esa misma referencia",
        estado_pasarela == 200 and en_pasarela.get("referencia") == cobro.get("idTransaccion"),
        en_pasarela,
    )


def idempotencia(token: str, poliza: str, cliente_id: str, estado: dict) -> None:
    escenario("2. Idempotencia: la misma orden no cobra dos veces")
    clave = _clave("e2e-idem")
    primero = _crear(token, clave, poliza, cliente_id)
    segundo = _crear(token, clave, poliza, cliente_id)
    comprobar(
        "reenviar la misma orden devuelve el mismo cobro",
        primero.get("id") == segundo.get("id"),
        segundo,
    )
    comprobar(
        "y el mismo estado y la misma referencia",
        (primero.get("estado"), primero.get("idTransaccion"))
        == (segundo.get("estado"), segundo.get("idTransaccion")),
        segundo,
    )
    distinto = _crear(token, clave, poliza, cliente_id, monto="1.00")
    comprobar(
        "la misma clave con otro monto da ErrorCobro (conflicto)",
        distinto.get("__typename") == "ErrorCobro",
        distinto,
    )
    _, en_pasarela = _pasarela_cobro(clave)
    comprobar(
        "la pasarela conserva la referencia original (sin segundo cobro)",
        isinstance(en_pasarela, dict)
        and en_pasarela.get("referencia") == primero.get("idTransaccion"),
        en_pasarela,
    )
    estado["idem"] = primero


def pasarela_lenta(token: str, poliza: str, cliente_id: str, estado: dict) -> None:
    escenario("3. Pasarela lenta: queda PENDIENTE y el reintento concilia sin cobrar de nuevo")
    clave = _clave("e2e-lento")
    _modo("lento")
    try:
        cobro = _crear(token, clave, poliza, cliente_id)
    finally:
        _modo("sano")
    comprobar(
        "con la pasarela lenta (> 700 ms) el cobro queda PENDIENTE (nunca un 5xx)",
        cobro.get("estado") == "PENDIENTE",
        cobro,
    )
    estado_pasarela, en_pasarela = _pasarela_cobro(clave)
    comprobar(
        "pero la pasarela SÍ cobró (el riesgo de doble cobro)",
        estado_pasarela == 200 and str(en_pasarela.get("referencia")).startswith("pg-"),
        en_pasarela,
    )
    referencia = en_pasarela.get("referencia") if isinstance(en_pasarela, dict) else None
    reintento, errores = _reintentar(token, cobro["id"])
    comprobar(
        "reintentarCobro lo deja COBRADO con la referencia que ya tenía la pasarela",
        bool(reintento)
        and reintento["estado"] == "COBRADO"
        and reintento["idTransaccion"] == referencia,
        reintento or errores,
    )
    de_nuevo, _ = _reintentar(token, cobro["id"])
    comprobar(
        "un segundo reintento (doble clic) devuelve lo mismo, sin tocar nada",
        bool(de_nuevo)
        and de_nuevo["estado"] == "COBRADO"
        and de_nuevo["idTransaccion"] == referencia,
        de_nuevo,
    )
    _, en_pasarela_despues = _pasarela_cobro(clave)
    comprobar(
        "la pasarela sigue con UNA sola referencia",
        isinstance(en_pasarela_despues, dict)
        and en_pasarela_despues.get("referencia") == referencia,
        en_pasarela_despues,
    )
    estado["lento"] = {"id": cobro["id"]}


def pasarela_caida(token: str, poliza: str, cliente_id: str, estado: dict) -> None:
    escenario("4. Pasarela caída (503): PENDIENTE sin cobro, y se cobra una sola vez al volver")
    _esperar_circuito()
    clave = _clave("e2e-caida")
    _modo("error")
    try:
        cobro = _crear(token, clave, poliza, cliente_id)
    finally:
        _modo("sano")
    comprobar(
        "el cobro queda PENDIENTE con un motivo, no un error",
        cobro.get("estado") == "PENDIENTE"
        and cobro.get("motivo") in ("pasarela_no_disponible", "circuito_abierto"),
        cobro,
    )
    estado_pasarela, _ = _pasarela_cobro(clave)
    comprobar(
        "la pasarela NO tiene el cobro (404): no se cobró nada",
        estado_pasarela == 404,
        estado_pasarela,
    )
    _esperar_circuito()
    final = _cobrar_al_volver(token, cobro["id"])
    comprobar(
        "al volver la pasarela, el reintento lo deja COBRADO",
        bool(final) and final["estado"] == "COBRADO",
        final,
    )
    estado_pasarela, en_pasarela = _pasarela_cobro(clave)
    comprobar(
        "con una sola referencia en la pasarela",
        estado_pasarela == 200
        and isinstance(final, dict)
        and en_pasarela.get("referencia") == final.get("idTransaccion"),
        en_pasarela,
    )
    estado["caida"] = {"id": cobro["id"]}


def pasarela_rechaza(token: str, poliza: str, cliente_id: str, estado: dict) -> None:
    escenario("5. Pasarela rechaza: reintentos manuales (máximo 3) y otro medio de pago")
    _esperar_circuito()
    clave = _clave("e2e-rechazo")
    _modo("rechaza")
    try:
        rechazado = _crear(token, clave, poliza, cliente_id)
        comprobar(
            "el cobro queda RECHAZADO por fondos_insuficientes con 3 reintentos",
            rechazado.get("estado") == "RECHAZADO"
            and rechazado.get("motivo") == "fondos_insuficientes"
            and rechazado.get("reintentosRestantes") == 3,
            rechazado,
        )
        restantes = []
        for _ in range(3):
            reintento, _ = _reintentar(token, rechazado["id"])
            restantes.append(
                (reintento or {}).get("estado") == "RECHAZADO" and reintento["reintentosRestantes"]
            )
        comprobar(
            "si la pasarela sigue rechazando, quedan 2, 1 y 0 reintentos",
            restantes == [2, 1, 0],
            restantes,
        )
        _, errores = _reintentar(token, rechazado["id"])
        mensaje = (errores or [{}])[0].get("message", "")
        comprobar(
            "sin reintentos, pide otro medio de pago",
            "medio de pago" in mensaje,
            mensaje or "sin error",
        )
        clave_2 = _clave("e2e-rechazo2")
        segundo = _crear(token, clave_2, poliza, cliente_id)
    finally:
        _modo("sano")
    reintento, errores = _reintentar(token, segundo["id"])
    comprobar(
        "si el cliente corrige y la pasarela acepta, el reintento cobra y deja 2",
        bool(reintento)
        and reintento["estado"] == "COBRADO"
        and reintento["reintentosRestantes"] == 2,
        reintento or errores,
    )
    estado_pasarela, _ = _pasarela_cobro(f"{clave_2}-r1")
    comprobar(
        "el reintento usó una referencia de comercio nueva (-r1)",
        estado_pasarela == 200,
        estado_pasarela,
    )
    estado["rechazo"] = {"id": rechazado["id"]}
    estado["corregido"] = {"id": segundo["id"]}


def tabla_y_permisos(token: str, poliza: str, estado: dict) -> None:
    escenario("6. Tabla de cobros del back-office y permisos")
    datos, errores = _gql(TABLA, {"p": poliza}, token)
    filas = {c["id"]: c for c in (datos or {}).get("cobros", [])}
    esperado = {
        "sano": "COBRADO",
        "idem": "COBRADO",
        "lento": "COBRADO",
        "caida": "COBRADO",
        "rechazo": "RECHAZADO",
        "corregido": "COBRADO",
    }
    faltan = [
        k
        for k, v in esperado.items()
        if k in estado and filas.get(estado[k]["id"], {}).get("estado") != v
    ]
    comprobar(
        f"la tabla muestra los {len(estado)} cobros de la prueba, cada uno con su estado",
        len(filas) >= len(estado) and not faltan,
        faltan or errores,
    )
    comprobar(
        "y el nombre del cliente",
        all((c["cliente"] or {}).get("nombre") for c in filas.values()),
        filas,
    )
    datos, errores = _gql(TABLA, {"p": poliza, "s": "RECHAZADO"}, token)
    ids = {c["id"] for c in (datos or {}).get("cobros", [])}
    comprobar(
        "filtrar por RECHAZADO muestra el rechazo y solo rechazados",
        estado["rechazo"]["id"] in ids
        and all(c["estado"] == "RECHAZADO" for c in (datos or {}).get("cobros", [])),
        errores,
    )
    datos, errores = _gql(TABLA, {"p": poliza})
    comprobar(
        "sin sesión no se ve ningún cobro", bool(errores) and not (datos or {}).get("cobros"), datos
    )


def escribir_evidencia(ruta: str, entorno: str) -> None:
    ok = sum(1 for _, _, r in resultados if r)
    lineas = [
        f"# Prueba de punta a punta de HU-W05 — {date.today().isoformat()}",
        "",
        f"Entorno: {entorno}. Script: `scripts/probar_cobros.py`.",
        f"Resultado: **{ok} de {len(resultados)} comprobaciones**.",
        "",
        "| Escenario | Comprobación | Resultado |",
        "|---|---|---|",
    ]
    lineas += [f"| {e} | {d} | {'OK' if r else 'FALLA'} |" for e, d, r in resultados]
    with open(ruta, "w", encoding="utf-8", newline="\n") as archivo:
        archivo.write("\n".join(lineas) + "\n")
    print(f"evidencia escrita en {ruta}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prueba de punta a punta de HU-W05 (cobros y conciliación)."
    )
    parser.add_argument(
        "--staging", metavar="PROYECTO", help="proyecto GCP de staging (p. ej. solventa-511815)"
    )
    parser.add_argument(
        "--usuario", default=None, help="BACKOFFICE (local: admin.solventa; staging: operador.demo)"
    )
    parser.add_argument(
        "--clave", default=None, help="clave del BACKOFFICE (mejor por CLAVE_BACKOFFICE o el vault)"
    )
    parser.add_argument(
        "--cliente",
        default=None,
        help="alias del CLIENTE (local: cliente.cobros; staging: cliente.demo)",
    )
    parser.add_argument(
        "--nombre-cliente", default="Cliente Cobros Demo", help="nombre si hay que crearlo"
    )
    parser.add_argument("--evidencia", metavar="RUTA", help="escribe el resultado en Markdown")
    argumentos = parser.parse_args()

    if argumentos.staging:
        Config.staging = argumentos.staging
        gcloud = shutil.which("gcloud") or "gcloud"
        salida = subprocess.run(
            [
                gcloud,
                "run",
                "services",
                "list",
                f"--project={argumentos.staging}",
                f"--region={REGION}",
                "--format=value(metadata.name,status.url)",
            ],
            capture_output=True,
            text=True,
        )
        if salida.returncode != 0:
            print(
                f"No pude listar los servicios de {argumentos.staging}: {salida.stderr.strip()}",
                file=sys.stderr,
            )
            return 2
        urls = dict(
            linea.split() for linea in salida.stdout.splitlines() if len(linea.split()) == 2
        )
        Config.bff = os.getenv("BFF_URL") or urls.get("bff", Config.bff)
        Config.payments = os.getenv("PAYMENTS_URL") or urls.get("payments", Config.payments)
        Config.acl = os.getenv("ACL_URL") or urls.get("acl-worker", Config.acl)
        Config.pasarela = os.getenv("STUB_PASARELA_URL") or urls.get(
            "stub-pasarela", Config.pasarela
        )
    argumentos.usuario = argumentos.usuario or (
        "operador.demo" if Config.staging else "admin.solventa"
    )
    argumentos.clave = argumentos.clave or (None if Config.staging else "admin-local-solventa")
    argumentos.cliente = argumentos.cliente or (
        "cliente.demo" if Config.staging else "cliente.cobros"
    )
    entorno = f"staging ({Config.staging})" if Config.staging else "local (docker compose)"
    print(f"HU-W05 de punta a punta — {entorno}\n")

    poliza = "POL-E2E-" + "".join(random.choices(string.ascii_uppercase, k=8))
    estado: dict = {}
    try:
        token, cliente_id = preparar(argumentos)
        print(f"   póliza de la prueba: {poliza}\n")
        try:
            cobro_sano(token, poliza, cliente_id, estado)
            idempotencia(token, poliza, cliente_id, estado)
            pasarela_lenta(token, poliza, cliente_id, estado)
            pasarela_caida(token, poliza, cliente_id, estado)
            pasarela_rechaza(token, poliza, cliente_id, estado)
            tabla_y_permisos(token, poliza, estado)
        finally:
            try:
                _modo("sano")
            except Fallo as error:
                print(f"AVISO: no pude dejar la pasarela en `sano`: {error}", file=sys.stderr)
    except Fallo as error:
        print(f"\nNO SE PUDO PROBAR: {error}", file=sys.stderr)
        return 2
    except KeyError as error:
        print(
            f"\nNO SE PUDO PROBAR: faltó un dato de una escena anterior ({error}).", file=sys.stderr
        )
        return 2

    ok = sum(1 for _, _, r in resultados if r)
    print(f"\nResultado: {ok} de {len(resultados)} comprobaciones en verde")
    if argumentos.evidencia:
        escribir_evidencia(argumentos.evidencia, entorno)
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
