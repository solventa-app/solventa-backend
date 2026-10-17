"""Humo de punta a punta del BFF contra el stack de docker compose (HU-W01 y HU-M02).

Recorre el flujo insignia por GraphQL, como lo haría el frontend, con usuario autenticado:
  0. iniciarSesion como BACKOFFICE, crearUsuario ASESOR (alias único) e iniciarSesion como ASESOR
  1. registrarConsentimiento con el token del ASESOR -> AUTH guarda al ASESOR como actor
  2. solicitarOferta con proveedores sanos             -> Oferta COMPLETA
  3. Open Finance lento (stub en modo `lento`)          -> Oferta PRELIMINAR (fuentes desde caché)
  4. revocarConsentimiento y volver a solicitar         -> ErrorOferta sin reintento
  5. el ASESOR intenta listar usuarios                  -> sin permiso (solo BACKOFFICE)
  6. crearCobro con la pasarela lenta y reintento        -> cobrado con UNA referencia (KAN-28)

Deja el stub otra vez en `sano` y espera a que los circuitos del ACL cierren, para que el
script se pueda repetir (el alias del ASESOR cambia en cada corrida). Solo biblioteca estándar.

Uso (con `docker compose --profile servicios up -d --build --wait`):
    python scripts/humo_bff.py

Las credenciales del BACKOFFICE por defecto son las SOLO LOCALES de la tarea
`crear-backoffice` de docker-compose (no son secretas)."""

import json
import os
import random
import string
import sys
import time
import urllib.request

BFF = os.getenv("BFF_URL", "http://localhost:8000") + "/graphql/web"
AUTH = os.getenv("AUTH_URL", "http://localhost:8001")
STUB_OPEN_FINANCE = os.getenv("STUB_OPEN_FINANCE_URL", "http://localhost:8101")
ACL = os.getenv("ACL_URL", "http://localhost:8005")
PAYMENTS = os.getenv("PAYMENTS_URL", "http://localhost:8004")
STUB_PASARELA = os.getenv("STUB_PASARELA_URL", "http://localhost:8103")
USUARIO_BACKOFFICE = os.getenv("USUARIO_BACKOFFICE", "admin.solventa")
CLAVE_BACKOFFICE = os.getenv("CLAVE_BACKOFFICE", "admin-local-solventa")

# Datos ficticios (sin datos reales).
ENTRADA = {
    # El BFF exige al menos 6 dígitos en el documento (mismo límite que el formulario W01-01).
    "documentoIdentidad": "CC-HUMO-100001",
    "valorCredito": "200000000",
    "plazoMeses": 240,
    "direccionInmueble": "Direccion de prueba",
}
CLAVE_ASESOR = "clave-humo-0001"

INICIAR_SESION = """mutation($entrada: CredencialesInput!) { iniciarSesion(entrada: $entrada) {
  __typename ... on Sesion { token usuario { id rol } } ... on ErrorAutenticacion { mensaje } } }"""
CREAR_USUARIO = """mutation($entrada: CrearUsuarioInput!) { crearUsuario(entrada: $entrada) {
  __typename ... on Usuario { id rol } ... on ErrorUsuario { mensaje } } }"""
REGISTRAR = """mutation { registrarConsentimiento(
  entrada: {finalidad: "cotizacion-vida-hipotecario", versionTexto: "v1"}) { id vigente } }"""
REVOCAR = "mutation($id: ID!) { revocarConsentimiento(id: $id) { id vigente } }"
SOLICITAR = """mutation($entrada: SolicitudOfertaInput!) { solicitarOferta(entrada: $entrada) {
  __typename
  ... on Oferta { tipo prima { valor } rangoPrima { minima { valor } maxima { valor } }
                  fuentes { nombre estado capturadoEn } }
  ... on ErrorOferta { mensaje puedeReintentar } } }"""


def _http(url: str, cuerpo: dict | None = None, token: str | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"}
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras)
    with urllib.request.urlopen(peticion, timeout=10) as respuesta:
        return json.loads(respuesta.read())


def _graphql_crudo(query: str, variables: dict | None = None, token: str | None = None) -> dict:
    return _http(BFF, {"query": query, "variables": variables or {}}, token)


def _graphql(query: str, variables: dict | None = None, token: str | None = None) -> dict:
    inicio = time.monotonic()
    cuerpo = _graphql_crudo(query, variables, token)
    ms = (time.monotonic() - inicio) * 1000
    if cuerpo.get("errors"):
        _fallar(f"errores de GraphQL: {cuerpo['errors']}")
    return {**cuerpo["data"], "_ms": round(ms)}


def _fallar(mensaje: str) -> None:
    print(f"FALLO: {mensaje}", file=sys.stderr)
    sys.exit(1)


def _esperar(condicion, descripcion: str, segundos: float = 30) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return
        time.sleep(1)
    _fallar(f"no se cumplió a tiempo: {descripcion}")


def _modo_open_finance(modo: str) -> None:
    _http(f"{STUB_OPEN_FINANCE}/control/modo", {"modo": modo})


def _iniciar_sesion(usuario: str, clave: str) -> dict:
    resultado = _graphql(INICIAR_SESION, {"entrada": {"usuario": usuario, "clave": clave}})
    sesion = resultado["iniciarSesion"]
    if sesion["__typename"] != "Sesion":
        _fallar(f"no se pudo iniciar sesión como {usuario}: {sesion}")
    return sesion


def _solicitar(consentimiento_id: str, token: str, **cambios) -> dict:
    entrada = {**ENTRADA, **cambios, "consentimientoId": consentimiento_id}
    respuesta = _graphql(SOLICITAR, {"entrada": entrada}, token)
    resultado = respuesta["solicitarOferta"]
    resumen = resultado.get("tipo") or resultado.get("mensaje")
    print(f"   -> {resultado['__typename']} {resumen} ({respuesta['_ms']} ms)")
    return resultado


def main() -> int:
    print("0. sesión: BACKOFFICE crea un ASESOR y el ASESOR inicia sesión")
    token_backoffice = _iniciar_sesion(USUARIO_BACKOFFICE, CLAVE_BACKOFFICE)["token"]
    # Sufijo de letras: un alias con 6 o más dígitos seguidos se rechaza por parecer un
    # documento (regla de AUTH), así que no se usa un timestamp.
    alias_asesor = "asesor.humo." + "".join(random.choices(string.ascii_lowercase, k=8))
    creado = _graphql(
        CREAR_USUARIO,
        {"entrada": {"usuario": alias_asesor, "clave": CLAVE_ASESOR, "rol": "ASESOR"}},
        token_backoffice,
    )["crearUsuario"]
    if creado["__typename"] != "Usuario":
        _fallar(f"no se pudo crear el ASESOR: {creado}")
    sesion_asesor = _iniciar_sesion(alias_asesor, CLAVE_ASESOR)
    token = sesion_asesor["token"]
    print(f"   -> ASESOR {alias_asesor} con sesión")

    print("1. registrarConsentimiento como ASESOR")
    consentimiento = _graphql(REGISTRAR, token=token)["registrarConsentimiento"]
    if not consentimiento["vigente"]:
        _fallar("el consentimiento recién registrado no está vigente")
    actor = _http(f"{AUTH}/consentimientos/{consentimiento['id']}").get("actor")
    if actor != {"rol": "ASESOR", "usuario_id": sesion_asesor["usuario"]["id"]}:
        _fallar(f"AUTH no registró al ASESOR como actor del consentimiento: {actor}")
    print("   -> AUTH guardó al ASESOR como actor (CA-W01-11)")

    print("2. solicitarOferta con proveedores sanos")
    oferta = _solicitar(consentimiento["id"], token)
    if oferta["__typename"] != "Oferta" or oferta["tipo"] != "COMPLETA":
        _fallar(f"se esperaba una oferta COMPLETA: {oferta}")
    if oferta["prima"] is None or oferta["rangoPrima"] is not None:
        _fallar("la oferta COMPLETA debe traer prima puntual y no rango")

    print("3. solicitarOferta con Open Finance lento")
    # Mismo documento del paso 2: el historial crediticio sale de la caché -> PRELIMINAR.
    # Documento nuevo: sin historial ni caché no hay oferta -> ErrorOferta con reintento
    # (CA-W01-05; el historial crediticio es la única fuente obligatoria).
    documento_sin_cache = "CC-HUMO-" + "".join(random.choices(string.digits, k=8))
    _modo_open_finance("lento")
    try:
        oferta = _solicitar(consentimiento["id"], token)
        sin_datos = _solicitar(
            consentimiento["id"], token, documentoIdentidad=documento_sin_cache
        )
    finally:
        _modo_open_finance("sano")
    if sin_datos["__typename"] != "ErrorOferta" or not sin_datos["puedeReintentar"]:
        _fallar(f"sin historial crediticio se esperaba ErrorOferta con reintento: {sin_datos}")
    if oferta["__typename"] != "Oferta" or oferta["tipo"] != "PRELIMINAR":
        _fallar(f"se esperaba una oferta PRELIMINAR: {oferta}")
    if oferta["rangoPrima"] is None:
        _fallar("la oferta PRELIMINAR debe traer rango de prima")
    degradadas = [f for f in oferta["fuentes"] if f["estado"] != "OK"]
    print(f"   fuentes no frescas: {[(f['nombre'], f['estado']) for f in degradadas]}")
    _esperar(
        lambda: set(_http(f"{ACL}/circuitos").values()) == {"closed"},
        "los circuitos del ACL vuelven a cerrar",
    )

    print("3b. solicitud con un plazo inválido")
    invalida = _solicitar(consentimiento["id"], token, plazoMeses=0)
    if invalida["__typename"] != "ErrorOferta" or invalida["puedeReintentar"]:
        _fallar(f"un plazo de 0 meses debe dar ErrorOferta sin reintento: {invalida}")

    print("4. revocarConsentimiento y volver a solicitar")
    revocado = _graphql(REVOCAR, {"id": consentimiento["id"]}, token)["revocarConsentimiento"]
    if revocado["vigente"]:
        _fallar("el consentimiento sigue vigente tras revocarlo")
    error = _solicitar(consentimiento["id"], token)
    if error["__typename"] != "ErrorOferta" or error["puedeReintentar"]:
        _fallar(f"se esperaba ErrorOferta sin reintento: {error}")

    print("5. el ASESOR intenta listar usuarios (solo BACKOFFICE)")
    cuerpo = _graphql_crudo("{ usuarios { id } }", token=token)
    mensaje = (cuerpo.get("errors") or [{}])[0].get("message")
    if mensaje != "No tienes permiso para esta operación.":
        _fallar(f"se esperaba un rechazo por permiso: {cuerpo}")
    print(f"   -> rechazado: {mensaje}")

    print("6. crearCobro con la pasarela lenta y reintento conciliado (back-office)")
    clave = "cobro-humo-" + "".join(random.choices(string.ascii_lowercase, k=8))
    nombre_cliente = "Cliente Humo Prueba"
    cliente = _graphql(
        CREAR_USUARIO,
        {
            "entrada": {
                "usuario": "cliente.humo." + "".join(random.choices(string.ascii_lowercase, k=8)),
                "clave": CLAVE_ASESOR,
                "rol": "CLIENTE",
                "nombre": nombre_cliente,
            }
        },
        token_backoffice,
    )["crearUsuario"]
    if cliente["__typename"] != "Usuario":
        _fallar(f"no se pudo crear el CLIENTE con nombre: {cliente}")
    _http(f"{STUB_PASARELA}/control/modo", {"modo": "lento"})
    try:
        cobro = _graphql(
            "mutation($e: CrearCobroInput!) { crearCobro(entrada: $e) { "
            "__typename ... on Cobro { id estado cliente { id nombre } } "
            "... on ErrorCobro { mensaje } } }",
            {
                "e": {
                    "idIntentoCobro": clave,
                    "polizaId": "POL-HUMO-1",
                    "clienteId": cliente["id"],
                    "monto": {"valor": "90000.00", "moneda": "COP"},
                    "tokenMedioPago": "tok_humo",
                }
            },
            token_backoffice,
        )["crearCobro"]
    finally:
        _http(f"{STUB_PASARELA}/control/modo", {"modo": "sano"})
    if cobro.get("estado") != "PENDIENTE":
        _fallar(f"con la pasarela lenta el cobro debía quedar pendiente: {cobro}")
    if cobro["cliente"] != {"id": cliente["id"], "nombre": nombre_cliente}:
        _fallar(f"el cobro debía llegar con el nombre del cliente: {cobro}")
    tabla = _graphql(
        "query($p: ID) { cobros(polizaId: $p) { cliente { nombre } } }",
        {"p": "POL-HUMO-1"},
        token_backoffice,
    )["cobros"]
    if not any(c["cliente"] and c["cliente"]["nombre"] == nombre_cliente for c in tabla):
        _fallar(f"la tabla de cobros debía mostrar el nombre del cliente: {tabla}")
    print("   -> la tabla de cobros muestra el nombre del cliente (cifrado en AUTH)")
    referencia_pasarela = _http(f"{STUB_PASARELA}/v1/cobros/{clave}")["referencia"]
    reintento = _graphql(
        "mutation($id: ID!) { reintentarCobro(cobroId: $id) { estado } }",
        {"id": cobro["id"]},
        token_backoffice,
    )["reintentarCobro"]
    final = _http(f"{PAYMENTS}/cobros/{cobro['id']}")
    if reintento["estado"] != "COBRADO" or final["referencia"] != referencia_pasarela:
        _fallar(f"el reintento debía conciliar con la pasarela sin cobrar de nuevo: {final}")
    print(f"   -> cobrado con la referencia original de la pasarela ({referencia_pasarela[:11]}…)")

    print("OK: humo del BFF en verde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
