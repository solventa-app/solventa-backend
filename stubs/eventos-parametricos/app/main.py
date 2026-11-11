"""Stub de las fuentes paramétricas externas (HU-M04, KAN-259, Fase 3): clima y vuelos.

Imita, en dos rutas, la forma mínima de respuesta de los proveedores reales que consume
`services/acl-worker/app/adapters/clima.py` y `.../adapters/vuelos.py`, para que el ACL Worker use
el MISMO código de adaptador contra este stub en local/CI o contra el proveedor real en la nube
(cambia solo `CLIMA_URL`/`VUELOS_URL`):

- `GET /v1/forecast` (Open-Meteo): devuelve una serie horaria sintética y determinista por celda
  (lat/lon redondeadas) para `precipitation`, `wind_speed_10m` o `temperature_2m`.
- `GET /v1/flights` (AviationStack): devuelve el retraso y el estado de un vuelo+fecha sintéticos
  y deterministas por `flight_iata`.

Mismo endpoint de control que los demás stubs (`/control/modo`: `sano`, `lento`, `error`,
`caido`) y, además, `/control/umbral` para forzar que la próxima consulta caiga por encima o por
debajo de un umbral (así se puede probar sin inventar una celda o un vuelo concretos). Andamiaje
de prueba: sin hexagonal, sin persistencia, datos inventados.
"""

import asyncio
import hashlib
import random
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.observabilidad import configurar_trazas

MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]
UmbralModo = Literal["sobre_umbral", "bajo_umbral", "automatico"]

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120

# Vuelos de demo (para QA, un número de vuelo fijo por caso) además de los sintéticos.
VUELOS_DEMO: dict[str, dict] = {
    "AV9340": {"delay": 145, "status": "active"},  # sobre el umbral típico (120 min)
    "AV1234": {"delay": 10, "status": "active"},  # bajo el umbral
    "LA5678": {"delay": 0, "status": "cancelled"},  # cancelado: paga aunque el retraso sea 0
    "NOOP00": None,  # vuelo que AviationStack no tiene (data vacío)
}


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudUmbral(BaseModel):
    modo: UmbralModo


def _serie_sintetica(parametro: str, latitud: float, longitud: float, forzar: str) -> list[float]:
    """24 valores horarios, deterministas por celda (igual que `_sintetico` de `stub-vehiculos`)."""
    celda = f"{round(latitud, 2)},{round(longitud, 2)}"
    semilla = int(hashlib.sha256(celda.encode()).hexdigest(), 16)
    base = {
        "precipitation": 1.0 + (semilla % 5),
        "wind_speed_10m": 8.0 + (semilla % 10),
        "temperature_2m": 18.0 + (semilla % 8),
    }[parametro]
    if forzar == "sobre_umbral":
        base *= 20
    elif forzar == "bajo_umbral":
        base *= 0.1
    return [round(base + (i % 3), 2) for i in range(24)]


def _vuelo_sintetico(numero_vuelo: str, forzar: str) -> dict | None:
    if numero_vuelo in VUELOS_DEMO:
        vuelo = VUELOS_DEMO[numero_vuelo]
        return dict(vuelo) if vuelo is not None else None
    semilla = int(hashlib.sha256(numero_vuelo.encode()).hexdigest(), 16)
    retraso = semilla % 60  # 0 a 59 min, por defecto bajo cualquier umbral razonable
    if forzar == "sobre_umbral":
        retraso = 150 + (semilla % 60)
    elif forzar == "bajo_umbral":
        retraso = semilla % 20
    return {"delay": retraso, "status": "active"}


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-eventos-parametricos", version="0.1.0")
    estado = {"modo": "sano", "umbral": "automatico"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _aplicar_latencia(modo: str) -> None:
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
        return {"servicio": "stub-eventos-parametricos", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.get("/control/umbral", tags=["control"])
    def obtener_umbral() -> dict[str, str]:
        return {"umbral": estado["umbral"]}

    @app.post("/control/umbral", tags=["control"])
    def fijar_umbral(body: SolicitudUmbral) -> dict[str, str]:
        """`sobre_umbral`: la próxima celda o vuelo sintético (cualquiera, no de la lista de demo)
        viene con un valor muy alto, para probar el cruce de umbral sin inventar una celda o un
        vuelo concretos. `bajo_umbral`: lo contrario. `automatico`: el valor sintético normal."""
        estado["umbral"] = body.modo
        return {"umbral": estado["umbral"]}

    # ------------------------------------------------------------------ clima (Open-Meteo)
    @app.get("/v1/forecast", tags=["proveedor"])
    async def forecast(
        latitude: float = Query(...),
        longitude: float = Query(...),
        hourly: str = Query(...),
        forecast_days: int = Query(1),
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _aplicar_latencia(modo_efectivo)
        parametros = [p.strip() for p in hourly.split(",") if p.strip()]
        if not parametros:
            raise HTTPException(status_code=422, detail="hourly vacío")
        horas = [f"2026-11-11T{h:02d}:00" for h in range(24)]
        datos_hourly: dict = {"time": horas}
        for parametro in parametros:
            if parametro not in ("precipitation", "wind_speed_10m", "temperature_2m"):
                raise HTTPException(status_code=422, detail=f"variable desconocida: {parametro}")
            datos_hourly[parametro] = _serie_sintetica(
                parametro, latitude, longitude, estado["umbral"]
            )
        return {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": datos_hourly,
            "generationtime_ms": 0.1,
        }

    # ------------------------------------------------------------------ vuelos (AviationStack)
    @app.get("/v1/flights", tags=["proveedor"])
    async def flights(
        flight_iata: str = Query(...),
        flight_date: str = Query(...),
        access_key: str = Query(""),
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        modo_efectivo = _modo_efectivo(modo, x_modo_simulado)
        await _aplicar_latencia(modo_efectivo)
        vuelo = _vuelo_sintetico(flight_iata.upper(), estado["umbral"])
        if vuelo is None:
            return {"data": []}
        return {
            "data": [
                {
                    "flight_date": flight_date,
                    "flight_status": vuelo["status"],
                    "departure": {"delay": vuelo["delay"]},
                    "flight": {"iata": flight_iata.upper()},
                }
            ]
        }

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-eventos-parametricos")

app = create_app()
