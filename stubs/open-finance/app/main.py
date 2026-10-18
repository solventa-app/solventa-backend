"""Stub de Open Finance (2 fuentes financieras, circular 004 de 2024 de la SFC).

Imita el contrato mínimo de un proveedor real: consulta síncrona por fuente y un
endpoint de control para forzar modos de falla en caliente (T-W05-2 / T-W01-8),
en el estilo de `stub-kyc` del Experimento 1 (`solventa-arquitectura@11e4be6`),
sin ciclo asíncrono crear→pollear (eso era específico de Truora/KYC, fuera de
alcance del Sprint 1) y sin estructura hexagonal (es andamiaje de prueba).
"""

import asyncio
import random
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.observabilidad import configurar_trazas

FUENTES = ("cuentas-bancarias", "historial-crediticio")
MODOS = ("sano", "lento", "error", "caido")
Modo = Literal["sano", "lento", "error", "caido"]

LATENCIA_LENTA_MIN_MS = 750
LATENCIA_LENTA_MAX_MS = 1100
LATENCIA_SANA_MIN_MS = 20
LATENCIA_SANA_MAX_MS = 120


class SolicitudModo(BaseModel):
    modo: Modo


class SolicitudConsulta(BaseModel):
    cliente_id: str
    consentimiento_id: str | None = None
    finalidad: str | None = None


DETALLE_SIN_CONSENTIMIENTO = "consentimiento_id y finalidad son obligatorios (CE 004/2024)"


def _datos_simulados(fuente: str) -> dict:
    if fuente == "cuentas-bancarias":
        return {"saldo_promedio_3m": 4_250_000, "productos_activos": 2}
    return {"score_crediticio": 712, "obligaciones_vigentes": 1}


def _validar_consentimiento_y_finalidad(
    consentimiento_id: str | None, finalidad: str | None
) -> None:
    if (
        not consentimiento_id
        or not consentimiento_id.strip()
        or not finalidad
        or not finalidad.strip()
    ):
        raise HTTPException(status_code=403, detail=DETALLE_SIN_CONSENTIMIENTO)


def create_app() -> FastAPI:
    app = FastAPI(title="solventa-stub-open-finance", version="0.1.0")
    estado = {"modo": "sano"}

    def _modo_efectivo(modo_qp: str | None, modo_header: str | None) -> str:
        """El query param o el header, si vienen, ganan por esa sola llamada (no persisten).
        Sin ninguno de los dos, se usa el modo global fijado por /control/modo."""
        modo = modo_qp or modo_header or estado["modo"]
        if modo not in MODOS:
            raise HTTPException(status_code=400, detail=f"modo inválido: {modo}")
        return modo

    async def _ejecutar_consulta(
        fuente: str,
        consentimiento_efectivo: str | None,
        finalidad_efectiva: str | None,
        modo: str | None,
        x_modo_simulado: str | None,
    ) -> dict:
        if fuente not in FUENTES:
            raise HTTPException(status_code=404, detail=f"fuente desconocida: {fuente}")

        _validar_consentimiento_y_finalidad(consentimiento_efectivo, finalidad_efectiva)

        modo_resuelto = _modo_efectivo(modo, x_modo_simulado)

        if modo_resuelto == "caido":
            # Simula un proveedor totalmente caído: el socket queda colgado hasta que
            # el cliente (adaptador del ACL Worker) cancele por su propio timeout duro.
            await asyncio.sleep(60)

        if modo_resuelto == "error":
            raise HTTPException(status_code=503, detail="proveedor_no_disponible")

        if modo_resuelto == "lento":
            await asyncio.sleep(random.uniform(LATENCIA_LENTA_MIN_MS, LATENCIA_LENTA_MAX_MS) / 1000)
        else:
            await asyncio.sleep(random.uniform(LATENCIA_SANA_MIN_MS, LATENCIA_SANA_MAX_MS) / 1000)

        return {
            "fuente": fuente,
            "datos": _datos_simulados(fuente),
            "capturado_en": datetime.now(UTC).isoformat(),
        }

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        # El health check siempre responde ok, sin importar el modo simulado: es la
        # sonda de vida del contenedor, no la sonda de recuperación del circuito.
        return {"servicio": "stub-open-finance", "estado": "ok"}

    @app.get("/control/modo", tags=["control"])
    def obtener_modo() -> dict[str, str]:
        return {"modo": estado["modo"]}

    @app.post("/control/modo", tags=["control"])
    def fijar_modo(body: SolicitudModo) -> dict[str, str]:
        estado["modo"] = body.modo
        return {"modo": estado["modo"]}

    @app.post("/v1/fuentes/{fuente}/consulta", tags=["proveedor"])
    async def consultar(
        fuente: str,
        body: SolicitudConsulta,
        modo: str | None = None,
        consentimiento_id: str | None = None,
        finalidad: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
        x_consentimiento_id: str | None = Header(default=None),
        x_finalidad: str | None = Header(default=None),
    ) -> dict:
        consentimiento_efectivo = (
            consentimiento_id
            if consentimiento_id is not None
            else (
                x_consentimiento_id
                if x_consentimiento_id is not None
                else body.consentimiento_id
            )
        )
        finalidad_efectiva = (
            finalidad
            if finalidad is not None
            else (x_finalidad if x_finalidad is not None else body.finalidad)
        )
        return await _ejecutar_consulta(
            fuente=fuente,
            consentimiento_efectivo=consentimiento_efectivo,
            finalidad_efectiva=finalidad_efectiva,
            modo=modo,
            x_modo_simulado=x_modo_simulado,
        )

    @app.api_route(
        "/open-finance/{fuente}/{cliente_id}",
        methods=["GET", "POST"],
        tags=["proveedor"],
    )
    async def consultar_por_ruta(
        fuente: str,
        cliente_id: str,
        modo: str | None = None,
        consentimiento_id: str | None = None,
        finalidad: str | None = None,
        x_modo_simulado: str | None = Header(default=None),
        x_consentimiento_id: str | None = Header(default=None),
        x_finalidad: str | None = Header(default=None),
    ) -> dict:
        _ = cliente_id
        consentimiento_efectivo = (
            consentimiento_id if consentimiento_id is not None else x_consentimiento_id
        )
        finalidad_efectiva = finalidad if finalidad is not None else x_finalidad
        return await _ejecutar_consulta(
            fuente=fuente,
            consentimiento_efectivo=consentimiento_efectivo,
            finalidad_efectiva=finalidad_efectiva,
            modo=modo,
            x_modo_simulado=x_modo_simulado,
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


configurar_trazas("stub-open-finance")

app = create_app()

