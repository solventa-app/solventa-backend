"""Prueba de punta a punta de HU-M04 (KAN-259) contra el docker compose local.

Recorre la cadena real, servicio por servicio, con un CLIENTE nuevo en cada corrida:

  F1  AUTH + BFF: el cliente entra desde la app con su dispositivo y registra el token de push.
  F3  ACL Worker: consulta el vuelo y el clima en `stub-eventos-parametricos` y arma el evento.
  F4  CLAIMS: cruza el evento con las pólizas de POLICY, pide la orden a PAYMENTS y aprueba solo;
      una reentrega no duplica nada; un vuelo bajo el umbral no liquida.
  F2  enviar-push: el push que pidió CLAIMS llega al teléfono registrado (SNS en modo stub).
  F5  BFF: `indemnizacion` y `miCoberturaParametrica` para el dueño, y `null` para otro cliente.
  D3  Una sola sesión en la app: entrar desde otro teléfono revoca la sesión anterior.

Sin Pub/Sub en local, el guion hace de mensajero: entrega a CLAIMS el evento que devolvió el ACL y
a `enviar-push` el push que CLAIMS dejó en su log (mismo contenido que `evento_push_indemnizacion`).
Las Fases 6 (móvil) y 7 (Terraform) no corren aquí.

Requisitos: `docker compose --profile servicios up -d --wait` arriba. Uso:

    python scripts/probar_m04_local.py

Solo usa la biblioteca estándar. Sale con 1 si alguna verificación falla.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import UTC, datetime

BFF = "http://localhost:8000/graphql/app"
ACL = "http://localhost:8005"
CLAIMS = "http://localhost:8012"
PAYMENTS = "http://localhost:8004"
AUTH = "http://localhost:8001"
ENVIAR_PUSH = "http://localhost:8013"
RED = "solventa-backend_default"
CLAVE = "Cliente-local-1"  # SOLO LOCAL, igual que `crear-cliente-siniestros-demo`

# Las llamadas van a localhost: sin proxy aunque el entorno tenga uno.
_abrir = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

fallas: list[str] = []


def verificar(condicion: bool, texto: str) -> None:
    print(f"  {'OK ' if condicion else 'FALLA'} {texto}")
    if not condicion:
        fallas.append(texto)


def http(metodo: str, url: str, cuerpo: dict | None = None, token: str | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(url, data=datos, method=metodo)
    peticion.add_header("content-type", "application/json")
    if token:
        peticion.add_header("authorization", f"Bearer {token}")
    with _abrir(peticion, timeout=30) as respuesta:
        return json.loads(respuesta.read() or b"null")


def gql(consulta: str, variables: dict | None = None, token: str | None = None) -> dict:
    return http("POST", BFF, {"query": consulta, "variables": variables or {}}, token)


def tarea(imagen: str, entorno: dict[str, str], *comando: str) -> str:
    """Corre una tarea de una sola vez (semilla, alta de cliente) con la imagen ya construida."""
    args = ["docker", "run", "--rm", "--network", RED]
    for clave, valor in entorno.items():
        args += ["-e", f"{clave}={valor}"]
    salida = subprocess.run([*args, imagen, *comando], capture_output=True, text=True, check=True)
    return salida.stdout + salida.stderr


def crear_cliente(alias: str, documento: str) -> None:
    tarea(
        "solventa/auth:local",
        {
            "DATABASE_URL": "postgresql://postgres:solventa-local@postgres:5432/auth",
            "CIFRADO_CLAVE": "c29sby1sb2NhbC1ub19zZWNyZXRhLWNpZnJhZG8tMDE=",
            "CLIENTE_ID_CLAVE": "solo-local-no-secreta",
            "CLAVE_INICIAL": CLAVE,
        },
        "python", "-m", "app.crear_cliente_demo", "--alias", alias,
        "--correo", f"{alias}@demo.solventa.co", "--nombre", "Cliente E2E M04",
        "--documento", documento,
    )  # fmt: skip


def entrar(alias: str, instalacion: str) -> tuple[str, str]:
    """Inicia sesión como lo hace la app (con `dispositivo`). Devuelve (token, usuario_id)."""
    r = gql(
        "mutation($e: CredencialesInput!){ iniciarSesion(entrada:$e){ __typename"
        " ... on Sesion { token } } }",
        {
            "e": {
                "usuario": f"{alias}@demo.solventa.co",
                "clave": CLAVE,
                "dispositivo": {"instalacionId": instalacion, "plataforma": "ANDROID"},
            }
        },
    )
    token = r["data"]["iniciarSesion"].get("token", "")
    carga = token.split(".")[1] if token else ""
    carga += "=" * (-len(carga) % 4)
    return token, json.loads(base64.urlsafe_b64decode(carga))["sub"] if token else ""


def consultar(cuerpo: dict) -> dict:
    return http("POST", f"{ACL}/eventos-parametricos/_consultar", cuerpo)


def logs_claims(desde: str) -> str:
    salida = subprocess.run(
        ["docker", "compose", "logs", "--no-log-prefix", "--since", desde, "claims"],
        capture_output=True, text=True,
    )  # fmt: skip
    return salida.stdout


def main() -> int:
    inicio = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sufijo = uuid.uuid4().hex[:8]
    alias, otro = f"e2e.m04.{sufijo}", f"e2e.m04.otro.{sufijo}"

    print("F1  AUTH + BFF: el cliente entra con su teléfono y registra el token de push")
    crear_cliente(alias, f"77{int(sufijo, 16) % 10**8:08d}")
    crear_cliente(otro, f"78{int(sufijo, 16) % 10**8:08d}")
    token, usuario = entrar(alias, f"inst-{sufijo}")
    verificar(bool(token), "iniciarSesion con dispositivo devuelve una Sesion")
    r = gql(
        "mutation($i:String!,$t:String!){ registrarTokenPush(instalacionId:$i, tokenFcm:$t) }",
        {"i": f"inst-{sufijo}", "t": f"fcm-e2e-{sufijo}"},
        token,
    )
    verificar(r.get("data", {}).get("registrarTokenPush") is True, "registrarTokenPush = true")

    salida = tarea(
        "solventa/policy:local",
        {
            "DATABASE_URL": "postgresql://postgres:solventa-local@postgres:5432/policy",
            "SEMILLA_CLIENTE_ID": usuario,
            "SEMILLA_TITULAR_ID": http("GET", f"{AUTH}/usuarios/{usuario}/identidad")["titular_id"],
            "SEMILLA_DIAS": "2",
        },
        "python", "-m", "app.sembrar_polizas_parametricas",
    )  # fmt: skip
    polizas = [
        linea.split("poliza=")[1].split()[0] for linea in salida.splitlines() if "poliza=" in linea
    ]
    verificar(len(polizas) == 2, "POLICY siembra 2 pólizas paramétricas del cliente")

    print("F3  ACL Worker: consulta los proveedores (stub) y arma el evento")
    vuelo = consultar({"tipo": "VUELO", "numero_vuelo": "AV9340", "fecha": "hoy"})
    clima = consultar(
        {"tipo": "CLIMA", "clave": "4.62,-74.09", "latitud": 4.62, "longitud": -74.09,
         "variable": "VIENTO_KMH"}
    )  # fmt: skip
    bajo = consultar({"tipo": "VUELO", "numero_vuelo": "AV1234", "fecha": "hoy"})
    verificar(vuelo["publicado"] and vuelo["evento"]["datos"]["valor"] == 145, "AV9340: +145 min")
    verificar(clima["publicado"] and clima["evento"]["datos"]["tipo"] == "CLIMA", "clima de Bogotá")
    verificar(bajo["publicado"], "AV1234 (10 min) también publica: el umbral lo decide CLAIMS")

    print("F4  CLAIMS + PAYMENTS: liquida sola, sin duplicar")
    r_vuelo = http("POST", f"{CLAIMS}/eventos/parametricos", vuelo["evento"])
    r_clima = http("POST", f"{CLAIMS}/eventos/parametricos", clima["evento"])
    r_otra = http("POST", f"{CLAIMS}/eventos/parametricos", vuelo["evento"])
    verificar(r_vuelo["creados"] >= 1, f"el vuelo crea el siniestro ({r_vuelo})")
    verificar(r_clima["creados"] >= 1, f"el clima crea el siniestro ({r_clima})")
    verificar(r_otra["creados"] == 0, "reentregar el mismo evento no crea otro (idempotente)")
    siniestros = []
    for poliza in polizas:
        siniestros += http("GET", f"{CLAIMS}/siniestros/parametricos?poliza_id={poliza}")
    verificar(len(siniestros) == 2, "un siniestro por póliza, ninguno duplicado")
    verificar(all(s["estado"] == "APROBADO" for s in siniestros), "los dos quedan APROBADO")
    for s in siniestros:
        detalle = http("GET", f"{CLAIMS}/siniestros/{s['id']}")
        orden = http("GET", f"{PAYMENTS}/indemnizaciones/{detalle['resolucion']['orden_pago_id']}")
        verificar(
            orden["monto"] == detalle["resolucion"]["indemnizacion"]["valor"]
            and orden["estado"] == "PENDIENTE_DISPERSION",
            f"PAYMENTS: orden de {s['ubicacion']['tipo']} ({orden['monto']} {orden['moneda']})",
        )
    time.sleep(1)
    pedidos = logs_claims(inicio).count("evento=notificacion.push-solicitada")
    verificar(pedidos >= 2, f"CLAIMS pide el push al dueño de la póliza ({pedidos} pedidos)")

    print("F2  enviar-push: el push llega al teléfono registrado")
    for s in siniestros:
        evento = {
            "eventId": str(uuid.uuid4()),
            "tipo": "notificacion.push-solicitada",
            "version": 1,
            "ocurridoEn": datetime.now(UTC).isoformat(),
            "datos": {
                "idempotencia": s["id"],
                "usuarioId": usuario,
                "plantilla": "indemnizacion-liquidada",
                "variables": {
                    "monto": s["suma_reclamada"]["valor"],
                    "moneda": s["suma_reclamada"]["moneda"],
                },
                "ruta": f"/m04-02/{s['id']}",
            },
        }
        resultado = http("POST", f"{ENVIAR_PUSH}/eventos", evento)["resultado"]
        verificar(resultado == "ENVIADO", f"push de {s['ubicacion']['tipo']}: {resultado}")

    print("F5  BFF: lo que ven M04-02/03 (detalle) y M04-04 (cobertura)")
    campos = (
        "id tipo referenciaEvento detalleEvento monto { valor moneda } estado metodoTransferencia"
    )
    for s in siniestros:
        r = gql(f'{{ indemnizacion(id: "{s["id"]}") {{ {campos} }} }}', token=token)
        dato = (r.get("data") or {}).get("indemnizacion")
        tipo = s["ubicacion"]["tipo"]
        verificar(dato is not None and dato["id"] == s["id"], f"indemnizacion {tipo}: {dato}")
    r = gql(
        "{ miCoberturaParametrica { producto numeroPoliza vigente tope { valor moneda }"
        " movimientos { id } } }",
        token=token,
    )
    cobertura = (r.get("data") or {}).get("miCoberturaParametrica")
    verificar(bool(cobertura and cobertura["vigente"]), f"miCoberturaParametrica: {cobertura}")
    verificar(
        cobertura is not None and len(cobertura["movimientos"]) >= 1,
        "la cobertura lista la indemnización liquidada",
    )
    token_otro, _ = entrar(otro, f"inst-otro-{sufijo}")
    r = gql(f'{{ indemnizacion(id: "{siniestros[0]["id"]}") {{ id }} }}', token=token_otro)
    verificar((r.get("data") or {}).get("indemnizacion") is None, "otro cliente recibe null")

    print("D3  una sola sesión en la app")
    token_nuevo, _ = entrar(alias, f"inst-nuevo-{sufijo}")
    r_viejo = gql("{ miCoberturaParametrica { numeroPoliza } }", token=token)
    r_nuevo = gql("{ miCoberturaParametrica { numeroPoliza } }", token=token_nuevo)
    verificar(bool(r_viejo.get("errors")), "el token del primer teléfono queda revocado")
    verificar(not r_nuevo.get("errors"), "el teléfono nuevo sí consulta")

    print(f"\n{'TODO OK' if not fallas else f'{len(fallas)} FALLAS'}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
