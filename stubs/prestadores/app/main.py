"""Stub de la red de prestadores de asistencia en sitio (HU-M06, contrato v0.49, B7).

Mientras no haya prestadores reales (clínicas, talleres y grúas que acepten o rechacen por su
sistema), este servicio decide el desenlace de cada asistencia que CLAIMS pide por el ACL Worker
(regla 2: CLAIMS no habla con el proveedor). Antes la simulación vivía dentro de CLAIMS.

- `PRESTADORES_SIMULACION=primero_falla` (por defecto): el primer intento de una asistencia no se
  confirma (`PRESTADOR_SIN_RESPUESTA`) y el reintento con el mismo id sí, para recorrer M06-04b y
  M06-04a en la demo. `confirma`: siempre confirma. No guarda estado: el intento lo manda CLAIMS.
- Minutos de llegada: línea recta (Haversine) a 20 km/h + 2 min de alistamiento, las mismas reglas
  de la app. Sin coordenadas del cliente (dirección a mano, hasta que se retire), sin tiempo.
  `PRESTADORES_MINUTOS_FIJOS=1` fija el tiempo (para probar «Mis asistencias» sin esperar).
- Modos de falla `sano`, `lento`, `error` y `caido`, como los demás stubs, para el Circuit Breaker.

Andamiaje de prueba: sin hexagonal, sin persistencia. La ubicación del cliente llega aquí (es adónde
debe ir el prestador) y nunca se escribe en los logs.
"""

import asyncio
import math
import os
import random
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from app.observabilidad import configurar_trazas

MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]
SIMULACIONES = ("primero_falla", "confirma")

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120

VELOCIDAD_KMH = 20
ALISTAMIENTO_MIN = 2
RADIO_TIERRA_KM = 6371


class SolicitudModo(BaseModel):
    modo: Modo


class PedidoAsistencia(BaseModel):
    asistencia_id: str = Field(min_length=1, max_length=64)
    intento: int = Field(ge=1)
    tipo_asistencia: str
    prestador_id: str
    prestador_latitud: float = Field(ge=-90, le=90)
    prestador_longitud: float = Field(ge=-180, le=180)
    latitud: float | None = Field(default=None, ge=-90, le=90)
    longitud: float | None = Field(default=None, ge=-180, le=180)


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta (Haversine)."""
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * RADIO_TIERRA_KM * math.asin(math.sqrt(h))


def minutos_estimados(km: float) -> int:
    return math.ceil(km / VELOCIDAD_KMH * 60) + ALISTAMIENTO_MIN


def _simulacion() -> str:
    valor = os.getenv("PRESTADORES_SIMULACION", "primero_falla").strip()
    if valor not in SIMULACIONES:
        raise ValueError(f"PRESTADORES_SIMULACION inválida: {valor}")
    return valor


def _minutos_fijos() -> int | None:
    valor = os.getenv("PRESTADORES_MINUTOS_FIJOS", "").strip()
    return int(valor) if valor else None


def desenlace(pedido: PedidoAsistencia, simulacion: str, minutos_fijos: int | None) -> dict:
    if simulacion == "primero_falla" and pedido.intento == 1:
        return {"estado": "NO_CONFIRMADA", "motivo": "PRESTADOR_SIN_RESPUESTA"}
    if pedido.latitud is None or pedido.longitud is None:
        minutos = None
    elif minutos_fijos is not None:
        minutos = minutos_fijos
    else:
        km = distancia_km(
            pedido.latitud, pedido.longitud, pedido.prestador_latitud, pedido.prestador_longitud
        )
        minutos = minutos_estimados(km)
    return {"estado": "CONFIRMADA", "minutos_estimados": minutos, "motivo": None}


def create_app() -> FastAPI:
    simulacion = _simulacion()
    app = FastAPI(title="stub-prestadores", version="0.1.0")
    estado = {"modo": "sano"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _simular_red(modo: str) -> None:
        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="proveedor_no_disponible")
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-prestadores", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.get("/v1/estado", tags=["proveedor"])
    async def estado_proveedor(
        modo: str | None = None, x_modo_simulado: str | None = Header(default=None)
    ) -> dict[str, str]:
        """Para la sonda de recuperación del circuito: obedece el modo, no confirma nada."""
        await _simular_red(_modo_efectivo(modo, x_modo_simulado))
        return {"estado": "ok"}

    @app.post("/v1/asistencias", tags=["proveedor"])
    async def solicitar(
        body: PedidoAsistencia,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        await _simular_red(_modo_efectivo(modo, x_modo_simulado))
        return desenlace(body, simulacion, _minutos_fijos())

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-prestadores")

app = create_app()
