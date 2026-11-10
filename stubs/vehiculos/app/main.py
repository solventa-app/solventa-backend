"""Stub de los proveedores de datos del vehículo (póliza de auto, KAN-290).

Tres fuentes en un solo servicio (decisión A-4 de `docs/plan-poliza-auto.md`), con el mismo
contrato y los mismos modos de falla que `stub-open-data`: una consulta síncrona por fuente y un
endpoint de control para forzar `sano`, `lento`, `error` o `caido` en caliente (Circuit Breaker del
ACL Worker).

- `runt-vehiculo`: lo que RUNT sabe de la placa (marca, línea, modelo, clase, servicio, estado y el
  odómetro de la última revisión técnico-mecánica).
- `fasecolda-valor`: el valor comercial de la referencia en la guía Fasecolda y su índice de hurto.
- `siniestralidad-placa`: los siniestros pagados a la placa en los últimos 3 años.

Las placas de demo (`vehiculos.json`) cubren cada caso de la tarifa; cualquier otra placa válida da
un vehículo sintético determinista, para que QA pueda inventar placas. Andamiaje de prueba: sin
hexagonal, sin persistencia, datos inventados.
"""

import asyncio
import hashlib
import json
import random
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.observabilidad import configurar_trazas

FUENTES = ("runt-vehiculo", "fasecolda-valor", "siniestralidad-placa")
MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120

# Placa colombiana: ABC123 (carros) o ABC12D (motos).
PLACA = re.compile(r"^[A-Z]{3}[0-9]{2}[A-Z0-9]$")
ANIO_ACTUAL = 2026

CATALOGO = json.loads((Path(__file__).parent / "vehiculos.json").read_text(encoding="utf-8"))

# Referencias para los vehículos sintéticos: (marca, línea, clase, valor nuevo, índice de hurto).
REFERENCIAS = [
    ("RENAULT", "SANDERO LIFE", "AUTOMOVIL", 62_000_000, "MEDIO"),
    ("CHEVROLET", "ONIX LT", "AUTOMOVIL", 71_000_000, "ALTO"),
    ("KIA", "SPORTAGE ZEUS", "CAMIONETA", 128_000_000, "MEDIO"),
    ("VOLKSWAGEN", "T-CROSS TRENDLINE", "CAMIONETA", 112_000_000, "BAJO"),
    ("TOYOTA", "COROLLA CROSS", "CAMIONETA", 139_000_000, "ALTO"),
    ("HYUNDAI", "ACCENT ADVANCE", "AUTOMOVIL", 76_000_000, "BAJO"),
]


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudConsulta(BaseModel):
    placa: str


class PlacaNoEncontrada(Exception):
    pass


class ReferenciaNoDisponible(Exception):
    pass


def _normalizar(placa: str) -> str:
    limpia = re.sub(r"[\s-]", "", placa).upper()
    if not PLACA.fullmatch(limpia):
        raise HTTPException(status_code=422, detail="placa_invalida")
    return limpia


def _sintetico(placa: str) -> dict:
    """Un vehículo plausible y siempre el mismo para la misma placa."""
    semilla = int(hashlib.sha256(placa.encode()).hexdigest(), 16)
    marca, linea, clase, valor_nuevo, indice = REFERENCIAS[semilla % len(REFERENCIAS)]
    if placa[-1].isalpha():  # placa de moto
        marca, linea, clase, valor_nuevo, indice = (
            "YAMAHA",
            "FZ 2.0",
            "MOTOCICLETA",
            13_500_000,
            "ALTO",
        )
    edad = semilla % 12  # 0 a 11 años
    modelo = ANIO_ACTUAL - edad
    valor = int(valor_nuevo * (0.9**edad)) // 100_000 * 100_000
    siniestros = [
        {
            "fecha": f"{ANIO_ACTUAL - 1 - i}-0{3 + i}-15",
            "tipo": "CHOQUE_PARCIAL",
            "valor": "1800000",
        }
        for i in range((semilla >> 8) % 3)
    ]
    return {
        "runt": {
            "marca": marca,
            "linea": linea,
            "modelo": modelo,
            "clase": clase,
            "servicio": "PARTICULAR",
            "codigo_fasecolda": f"SIN{semilla % 10**5:05d}",
            "estado": "ACTIVO",
            "odometro_km": 8_000 + edad * 13_000 + semilla % 5_000,
            "fecha_ultima_rtm": None,
        },
        "fasecolda": {"valor_comercial": str(valor), "indice_hurto": indice},
        "siniestros": siniestros,
    }


def _vehiculo(placa: str) -> dict:
    if placa in CATALOGO["no_encontradas"]:
        raise PlacaNoEncontrada()
    return CATALOGO["placas"].get(placa) or _sintetico(placa)


def _datos(fuente: str, placa: str) -> dict:
    vehiculo = _vehiculo(placa)
    if fuente == "runt-vehiculo":
        return {"placa": placa, **vehiculo["runt"]}
    if fuente == "fasecolda-valor":
        if vehiculo["fasecolda"] is None:
            raise ReferenciaNoDisponible()
        return {
            "codigo_fasecolda": vehiculo["runt"]["codigo_fasecolda"],
            "modelo": vehiculo["runt"]["modelo"],
            **vehiculo["fasecolda"],
        }
    return {
        "placa": placa,
        "siniestros_3_anios": len(vehiculo["siniestros"]),
        "detalle": vehiculo["siniestros"],
    }


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-vehiculos", version="0.1.0")
    estado = {"modo": "sano"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "stub-vehiculos", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.get("/control/placas", tags=["control"])
    def placas_demo() -> dict[str, str]:
        """Las placas de demo y el caso que cubre cada una (para QA)."""
        return {placa: datos["caso"] for placa, datos in CATALOGO["placas"].items()}

    @app.post("/v1/fuentes/{fuente}/consulta", tags=["proveedor"])
    async def consultar(
        fuente: str,
        body: SolicitudConsulta,
        modo: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
    ) -> dict:
        if fuente not in FUENTES:
            raise HTTPException(status_code=404, detail=f"fuente desconocida: {fuente}")
        placa = _normalizar(body.placa)
        modo = _modo_efectivo(modo, x_modo_simulado)

        if modo == "caido":
            await asyncio.sleep(60)
        if modo == "error":
            raise HTTPException(status_code=503, detail="proveedor_no_disponible")
        if modo == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

        try:
            datos = _datos(fuente, placa)
        except PlacaNoEncontrada as error:
            raise HTTPException(status_code=404, detail="placa_no_encontrada") from error
        except ReferenciaNoDisponible as error:
            raise HTTPException(status_code=503, detail="proveedor_no_disponible") from error
        return {"fuente": fuente, "datos": datos, "capturado_en": datetime.now(UTC).isoformat()}

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-vehiculos")

app = create_app()
