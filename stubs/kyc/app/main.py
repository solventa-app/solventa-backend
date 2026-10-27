"""Stub del proveedor de KYC (verificación de identidad), HU-W03, decisión W03-10.

Mismos modos de falla que los demás stubs (`sano`, `lento`, `error`, `caido`) más `rechaza` (el
proveedor responde, pero no puede verificar la identidad), fijados en caliente vía `/control/modo` o
por llamada vía query param `modo`/header `X-Modo-Simulado`.

Como un proveedor real, **recuerda cada verificación por su referencia** (`referencia_comercio`):
repetirla devuelve el resultado original. El modo `lento` registra la verificación **antes** de la
espera, así que si el ACL Worker corta a los 700 ms, la verificación igual quedó hecha.

**El contrato es inventado**, no el de un proveedor real (Truora u otro): un KYC real pide el
documento del cliente y una validación (p. ej. biométrica), y eso no pasa por este stub. Aquí solo
llega el identificador opaco del cliente (regla de arquitectura #6) y el resultado es determinista:
`aprobada`, salvo en modo `rechaza`. Ver `docs/plan-completar-hu-w03.md`, decisión W03-10.

**HU-M01 (KAN-31): imita las APIs independientes de Didit v3** (`/v3/id-verification/`,
`/v3/passive-liveness/` y `/v3/face-match/`), con su misma forma de petición (multipart y
`x-api-key`) y de respuesta, para que el ACL Worker use el mismo adaptador contra el stub y contra
Didit. El caso lo decide un marcador de texto dentro de la imagen de prueba (`CASO=<caso>` y,
opcional, `NUMERO=<cédula>`); sin marcador, todo sale aprobado con datos ficticios, como el
sandbox de Didit. Casos: `ilegible` y `vencido` (anverso), `menor` (anverso, aprobado pero con
fecha de nacimiento de un menor: la edad la decide ONBOARDING), `suplantacion` y
`rostro-distinto` (fotograma del rostro). Ver el README.

Expone además `GET /v1/estado`, que **sí obedece el modo simulado** (a diferencia de `/health`): lo
usa la sonda de recuperación del circuito del ACL Worker, que no debe crear una verificación de
verdad solo para probar si el proveedor ya respondió.
"""

import asyncio
import random
import re
import uuid
from datetime import UTC, date, datetime
from email.parser import BytesParser
from email.policy import HTTP
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.observabilidad import configurar_trazas

MODOS = ("sano", "lento", "error", "caido", "rechaza")
Modo = Literal["sano", "lento", "error", "caido", "rechaza"]
MOTIVO_RECHAZO = "identidad_no_verificada"

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


MARCADOR = re.compile(rb"(CASO|NUMERO)=([A-Za-z0-9-]{1,32})")
NUMERO_POR_DEFECTO = "1000000001"


def _marcadores(contenido: bytes) -> dict[str, str]:
    return {clave.decode(): valor.decode() for clave, valor in MARCADOR.findall(contenido)}


def _partes_multipart(tipo_contenido: str, cuerpo: bytes) -> dict[str, bytes]:
    """Lee un multipart/form-data con la librería estándar (sin dependencias nuevas)."""
    mensaje = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + tipo_contenido.encode() + b"\r\n\r\n" + cuerpo
    )
    partes: dict[str, bytes] = {}
    for parte in mensaje.iter_parts():
        nombre = parte.get_param("name", header="content-disposition")
        if nombre:
            partes[nombre] = parte.get_payload(decode=True) or b""
    return partes


def _alerta(riesgo: str, caracteristica: str, descripcion: str) -> dict:
    return {
        "risk": riesgo,
        "feature": caracteristica,
        "additional_data": None,
        "log_type": "error",
        "short_description": descripcion,
        "long_description": descripcion,
    }


def _verificacion_documento(anverso: bytes) -> dict:
    marcas = _marcadores(anverso)
    caso = marcas.get("CASO")
    hoy = date.today()
    nacimiento = (
        date(hoy.year - 16, hoy.month, min(hoy.day, 28)) if caso == "menor" else date(1995, 4, 12)
    )
    alertas = []
    if caso == "ilegible":
        alertas.append(_alerta("DOCUMENT_NOT_READABLE", "ID_VERIFICATION", "Document not readable"))
    if caso == "vencido":
        alertas.append(_alerta("DOCUMENT_EXPIRED", "ID_VERIFICATION", "Document expired"))
    legible = caso != "ilegible"
    return {
        "status": "Declined" if alertas else "Approved",
        "document_type": "Identity Card" if legible else None,
        "document_number": marcas.get("NUMERO", NUMERO_POR_DEFECTO) if legible else None,
        "first_name": "Usuario" if legible else None,
        "last_name": "De Prueba" if legible else None,
        "full_name": "Usuario De Prueba" if legible else None,
        "date_of_birth": nacimiento.isoformat() if legible else None,
        "age": hoy.year - nacimiento.year if legible else None,
        "expiration_date": "2020-01-01" if caso == "vencido" else "2034-01-01",
        "issuing_state": "COL",
        "issuing_state_name": "Colombia",
        "warnings": alertas,
    }


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudVerificacion(BaseModel):
    # "Comercio" es el punto de vista del proveedor: el comercio somos nosotros. Solventa la llama
    # `id_verificacion` en su dominio y el adaptador del ACL Worker traduce entre los dos nombres.
    referencia_comercio: str
    cliente_id: str


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-kyc", version="0.1.0")
    estado = {"modo": "sano"}
    # Verificaciones registradas por referencia del comercio.
    verificaciones: dict[str, dict] = {}
    candado = asyncio.Lock()

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _fallar_si_corresponde(modo: str) -> None:
        """Fallas que ocurren ANTES de procesar nada: no registran la verificación."""
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="kyc_no_disponible")

    async def _esperar_respuesta(modo: str) -> None:
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-kyc", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.post("/v1/verificaciones", tags=["proveedor"])
    async def verificar(
        body: SolicitudVerificacion,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        async with candado:
            resultado = verificaciones.get(body.referencia_comercio)
            if resultado is None:
                # Verificación nueva. Se registra ANTES de la espera de `lento`: si el cliente
                # corta, igual quedó hecha (como en la vida real).
                rechazada = modo_efectivo == "rechaza"
                resultado = {
                    "estado": "rechazada" if rechazada else "aprobada",
                    "referencia": None if rechazada else f"kyc-{uuid.uuid4()}",
                    "motivo": MOTIVO_RECHAZO if rechazada else None,
                    "verificada_en": datetime.now(UTC).isoformat(),
                }
                verificaciones[body.referencia_comercio] = resultado
        await _esperar_respuesta(modo_efectivo)
        return resultado

    @app.get("/v1/verificaciones/{referencia_comercio}", tags=["proveedor"])
    async def consultar_verificacion(referencia_comercio: str) -> dict:
        """Para verificar en vivo qué quedó registrado: 404 si el proveedor no la tiene."""
        resultado = verificaciones.get(referencia_comercio)
        if resultado is None:
            raise HTTPException(status_code=404, detail="verificación no encontrada")
        return resultado

    async def _didit(
        request: Request, modo: str | None, modo_header: str | None, api_key: str | None
    ) -> dict[str, bytes]:
        """Lo común de las tres rutas al estilo Didit: llave, modo simulado y multipart."""
        if not api_key:
            raise HTTPException(status_code=403, detail="Invalid API key")
        modo_efectivo = _modo_efectivo(modo, modo_header)
        await _fallar_si_corresponde(modo_efectivo)
        partes = _partes_multipart(request.headers.get("content-type", ""), await request.body())
        await _esperar_respuesta(modo_efectivo)
        return partes

    def _envoltura(clave: str, bloque: dict, partes: dict[str, bytes]) -> dict:
        return {
            "request_id": str(uuid.uuid4()),
            clave: bloque,
            "vendor_data": (partes.get("vendor_data") or b"").decode() or None,
            "metadata": None,
            "created_at": datetime.now(UTC).isoformat(),
        }

    @app.post("/v3/id-verification/", tags=["didit"])
    async def didit_documento(
        request: Request,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        partes = await _didit(request, modo, x_modo_simulado, x_api_key)
        if "front_image" not in partes:
            raise HTTPException(status_code=400, detail="front_image is required")
        return _envoltura("id_verification", _verificacion_documento(partes["front_image"]), partes)

    @app.post("/v3/passive-liveness/", tags=["didit"])
    async def didit_vida(
        request: Request,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        partes = await _didit(request, modo, x_modo_simulado, x_api_key)
        if "user_image" not in partes:
            raise HTTPException(status_code=400, detail="user_image is required")
        ataque = _marcadores(partes["user_image"]).get("CASO") == "suplantacion"
        bloque = {
            "status": "Declined" if ataque else "Approved",
            "method": "PASSIVE",
            "score": 12.5 if ataque else 95,
            "warnings": [_alerta("LIVENESS_FACE_ATTACK", "LIVENESS", "Face attack")]
            if ataque
            else [],
        }
        return _envoltura("liveness", bloque, partes)

    @app.post("/v3/face-match/", tags=["didit"])
    async def didit_rostro(
        request: Request,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
    ) -> dict:
        partes = await _didit(request, modo, x_modo_simulado, x_api_key)
        if "user_image" not in partes or "ref_image" not in partes:
            raise HTTPException(status_code=400, detail="user_image and ref_image are required")
        distinto = _marcadores(partes["user_image"]).get("CASO") == "rostro-distinto"
        bloque = {
            "status": "Declined" if distinto else "Approved",
            "score": 24.87 if distinto else 99.41,
            "warnings": [
                _alerta("LOW_FACE_MATCH_SIMILARITY", "FACEMATCH", "Low face match similarity")
            ]
            if distinto
            else [],
        }
        return _envoltura("face_match", bloque, partes)

    @app.get("/", tags=["didit"])
    async def raiz() -> dict:
        """Lo que sondea el ACL Worker contra Didit (que no tiene un endpoint de estado)."""
        return {"ok": True}

    @app.get("/v1/estado", tags=["proveedor"])
    async def estado_proveedor(
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        """Usado solo por la sonda de recuperación del circuito (ver docstring del módulo)."""
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _fallar_si_corresponde(modo_efectivo)
        await _esperar_respuesta(modo_efectivo)
        return {"ok": True}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-kyc")

app = create_app()
