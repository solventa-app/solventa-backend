"""Humo de punta a punta del BFF contra el stack de docker compose (HU-W01).

Recorre el flujo insignia por GraphQL, como lo haría el frontend:
  1. registrarConsentimiento
  2. solicitarOferta con proveedores sanos             -> Oferta COMPLETA
  3. Open Finance lento (stub en modo `lento`)          -> Oferta PRELIMINAR (fuentes desde caché)
  4. revocarConsentimiento y volver a solicitar         -> ErrorOferta sin reintento

Deja el stub otra vez en `sano` y espera a que los circuitos del ACL cierren, para
que el script se pueda repetir. Solo biblioteca estándar.

Uso (con `docker compose --profile servicios up -d --build --wait`):
    python scripts/humo_bff.py
"""

import json
import os
import sys
import time
import urllib.request

BFF = os.getenv("BFF_URL", "http://localhost:8000") + "/graphql/web"
STUB_OPEN_FINANCE = os.getenv("STUB_OPEN_FINANCE_URL", "http://localhost:8101")
ACL = os.getenv("ACL_URL", "http://localhost:8005")

# Documento ficticio (sin datos reales).
ENTRADA = {
    "documentoIdentidad": "CC-HUMO-0001",
    "valorCredito": "200000000",
    "plazoMeses": 240,
    "direccionInmueble": "Direccion de prueba",
}

REGISTRAR = """mutation { registrarConsentimiento(
  entrada: {finalidad: "cotizacion-vida-hipotecario", versionTexto: "v1"}) { id vigente } }"""
REVOCAR = "mutation($id: ID!) { revocarConsentimiento(id: $id) { id vigente } }"
SOLICITAR = """mutation($entrada: SolicitudOfertaInput!) { solicitarOferta(entrada: $entrada) {
  __typename
  ... on Oferta { tipo prima { valor } rangoPrima { minima { valor } maxima { valor } }
                  fuentes { nombre estado capturadoEn } }
  ... on ErrorOferta { mensaje puedeReintentar } } }"""


def _http(url: str, cuerpo: dict | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(peticion, timeout=10) as respuesta:
        return json.loads(respuesta.read())


def _graphql(query: str, variables: dict | None = None) -> dict:
    inicio = time.monotonic()
    cuerpo = _http(BFF, {"query": query, "variables": variables or {}})
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


def _solicitar(consentimiento_id: str) -> dict:
    respuesta = _graphql(SOLICITAR, {"entrada": {**ENTRADA, "consentimientoId": consentimiento_id}})
    resultado = respuesta["solicitarOferta"]
    resumen = resultado.get("tipo") or resultado.get("mensaje")
    print(f"   -> {resultado['__typename']} {resumen} ({respuesta['_ms']} ms)")
    return resultado


def main() -> int:
    print("1. registrarConsentimiento")
    consentimiento = _graphql(REGISTRAR)["registrarConsentimiento"]
    if not consentimiento["vigente"]:
        _fallar("el consentimiento recién registrado no está vigente")

    print("2. solicitarOferta con proveedores sanos")
    oferta = _solicitar(consentimiento["id"])
    if oferta["__typename"] != "Oferta" or oferta["tipo"] != "COMPLETA":
        _fallar(f"se esperaba una oferta COMPLETA: {oferta}")
    if oferta["prima"] is None or oferta["rangoPrima"] is not None:
        _fallar("la oferta COMPLETA debe traer prima puntual y no rango")

    print("3. solicitarOferta con Open Finance lento")
    _modo_open_finance("lento")
    try:
        oferta = _solicitar(consentimiento["id"])
    finally:
        _modo_open_finance("sano")
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

    print("4. revocarConsentimiento y volver a solicitar")
    revocado = _graphql(REVOCAR, {"id": consentimiento["id"]})["revocarConsentimiento"]
    if revocado["vigente"]:
        _fallar("el consentimiento sigue vigente tras revocarlo")
    error = _solicitar(consentimiento["id"])
    if error["__typename"] != "ErrorOferta" or error["puedeReintentar"]:
        _fallar(f"se esperaba ErrorOferta sin reintento: {error}")

    print("OK: humo del BFF en verde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
