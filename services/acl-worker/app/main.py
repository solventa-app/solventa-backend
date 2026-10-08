"""ACL Worker: capa anticorrupción hexagonal. Único punto de contacto con
proveedores externos (Open Finance, Open Data, pasarela de pagos) — ver
`app/domain/puertos.py` y el README de este servicio.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
import rq
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.adapters.open_data import FUENTES_SOPORTADAS as FUENTES_OPEN_DATA
from app.adapters.open_data import AdaptadorOpenData
from app.adapters.open_finance import FUENTES_SOPORTADAS as FUENTES_OPEN_FINANCE
from app.adapters.open_finance import AdaptadorOpenFinance
from app.adapters.pasarela import AdaptadorPasarela
from app.application.cache import CacheFuentes
from app.application.circuitos import (
    NOMBRE_CIRCUITO_OPEN_DATA,
    NOMBRE_CIRCUITO_OPEN_FINANCE,
    NOMBRE_CIRCUITO_PASARELA,
    construir_fabrica,
)
from app.application.configuracion import Configuracion
from app.application.reconciliacion import (
    NOMBRE_COLA,
    ColaReconciliacionRQ,
    MarcaDedupRedis,
    ProductorReconciliacion,
)
from app.application.servicio_fuentes import ServicioConsultaFuentes
from app.application.servicio_pagos import ServicioCobro
from app.application.sonda import SondaRecuperacion
from app.domain.puertos import ConsultaFuente, OrdenCobro

# Backoff acotado de la reconciliación diferida (contrato punto 5): 5 intentos,
# intervalos crecientes ~10s/30s/60s/120s/300s. Agotados, `rq` mueve el job al
# `FailedJobRegistry` (equivalente a la DLQ del diseño original) en vez de
# reintentar indefinidamente u ocultarlo.
REINTENTOS_RECONCILIACION = rq.Retry(max=5, interval=[10, 30, 60, 120, 300])

# Sin esto, los logs INFO de app.application.sonda (único rastro observable de
# que la sonda, no una petición de usuario, cerró el circuito) se descartan en
# silencio: uvicorn solo configura sus propios loggers ("uvicorn", "uvicorn.access"),
# no el root, y sin un handler el "lastResort" de logging filtra por debajo de WARNING.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class ConsultaFuenteRequest(BaseModel):
    cliente_id: str
    consentimiento_id: str
    fuente: str


class DatosFuenteResponse(BaseModel):
    fuente: str
    datos: dict
    capturado_en: str
    de_cache: bool
    degradado: bool


class OrdenCobroRequest(BaseModel):
    clave_idempotencia: str
    poliza_id: str
    monto: str
    moneda: str
    token_medio_pago: str


class ResultadoCobroResponse(BaseModel):
    estado: str
    referencia: str | None
    motivo: str | None
    detalle: dict


def _construir_servicios_fuentes(
    config: Configuracion,
    fabrica,
    cache_redis,
    productor_reconciliacion,
) -> tuple[dict[str, ServicioConsultaFuentes], AdaptadorOpenFinance, AdaptadorOpenData]:
    adaptador_open_finance = AdaptadorOpenFinance(config.open_finance_url, config.timeout_s)
    adaptador_open_data = AdaptadorOpenData(config.open_data_url, config.timeout_s)
    cache = CacheFuentes(cache_redis, config.cache_ttl_segundos)

    servicio_open_finance = ServicioConsultaFuentes(
        adaptador_open_finance,
        fabrica,
        NOMBRE_CIRCUITO_OPEN_FINANCE,
        cache,
        config.timeout_s,
        productor_reconciliacion,
    )
    servicio_open_data = ServicioConsultaFuentes(
        adaptador_open_data,
        fabrica,
        NOMBRE_CIRCUITO_OPEN_DATA,
        cache,
        config.timeout_s,
        productor_reconciliacion,
    )

    servicios_por_fuente = {fuente: servicio_open_finance for fuente in FUENTES_OPEN_FINANCE}
    servicios_por_fuente.update({fuente: servicio_open_data for fuente in FUENTES_OPEN_DATA})

    return servicios_por_fuente, adaptador_open_finance, adaptador_open_data


def create_app(config: Configuracion | None = None) -> FastAPI:
    config = config or Configuracion.desde_entorno()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cache_redis = redis.Redis.from_url(config.redis_url)
        fabrica = construir_fabrica(config.breaker_umbral_fallos, config.breaker_ttl_segundos)

        # db de Redis separado (db 2) del de la caché (db 1): productor de la
        # reconciliación diferida que consume `services/consolidador-fuentes`.
        redis_cola = redis.Redis.from_url(config.cola_reconciliacion_redis_url)
        cola_rq = rq.Queue(NOMBRE_COLA, connection=redis_cola)
        productor_reconciliacion = ProductorReconciliacion(
            cola=ColaReconciliacionRQ(cola_rq, REINTENTOS_RECONCILIACION),
            dedup=MarcaDedupRedis(redis_cola),
        )

        servicios_por_fuente, adaptador_open_finance, adaptador_open_data = (
            _construir_servicios_fuentes(config, fabrica, cache_redis, productor_reconciliacion)
        )

        adaptador_pasarela = AdaptadorPasarela(config.pasarela_url, config.timeout_s)
        servicio_cobro = ServicioCobro(
            adaptador_pasarela, fabrica, NOMBRE_CIRCUITO_PASARELA, config.timeout_s
        )

        sonda = SondaRecuperacion(
            fabrica,
            sondeos={
                NOMBRE_CIRCUITO_OPEN_FINANCE: adaptador_open_finance.sondear,
                NOMBRE_CIRCUITO_OPEN_DATA: adaptador_open_data.sondear,
                NOMBRE_CIRCUITO_PASARELA: adaptador_pasarela.sondear,
            },
            intervalo_s=config.sonda_intervalo_segundos,
        )
        sonda.iniciar()

        app.state.fabrica_circuitos = fabrica
        app.state.servicios_por_fuente = servicios_por_fuente
        app.state.servicio_cobro = servicio_cobro

        try:
            yield
        finally:
            await sonda.detener()
            cierres = (
                adaptador_open_finance.cerrar,
                adaptador_open_data.cerrar,
                adaptador_pasarela.cerrar,
            )
            for cerrar in cierres:
                cerrar()
            cache_redis.close()
            redis_cola.close()

    app = FastAPI(title="solventa-acl-worker", version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["operacion"])
    def health() -> dict[str, str]:
        return {"servicio": "acl-worker", "estado": "ok"}

    @app.get("/circuitos", tags=["operacion"])
    def circuitos() -> dict[str, str]:
        """Observabilidad del estado del Circuit Breaker, sin datos personales
        (solo nombres de circuito y su estado) — útil para verificar en vivo
        que abre, hace fail-fast y cierra solo (EC009/EC010)."""
        fabrica = app.state.fabrica_circuitos
        nombres = (
            NOMBRE_CIRCUITO_OPEN_FINANCE,
            NOMBRE_CIRCUITO_OPEN_DATA,
            NOMBRE_CIRCUITO_PASARELA,
        )
        return {nombre: fabrica.get_breaker(nombre).context.state for nombre in nombres}

    @app.post("/fuentes/consultar", tags=["fuentes"], response_model=DatosFuenteResponse)
    def consultar_fuente(body: ConsultaFuenteRequest) -> DatosFuenteResponse:
        servicio = app.state.servicios_por_fuente.get(body.fuente)
        if servicio is None:
            raise HTTPException(status_code=404, detail=f"fuente desconocida: {body.fuente}")

        consulta = ConsultaFuente(
            cliente_id=body.cliente_id,
            consentimiento_id=body.consentimiento_id,
            fuente=body.fuente,
        )
        datos = servicio.consultar(consulta)
        return DatosFuenteResponse(
            fuente=datos.fuente,
            datos=datos.datos,
            capturado_en=datos.capturado_en.isoformat(),
            de_cache=datos.de_cache,
            degradado=datos.degradado,
        )

    @app.post("/pagos/cobrar", tags=["pagos"], response_model=ResultadoCobroResponse)
    def cobrar(body: OrdenCobroRequest) -> ResultadoCobroResponse:
        orden = OrdenCobro(
            clave_idempotencia=body.clave_idempotencia,
            poliza_id=body.poliza_id,
            monto=body.monto,
            moneda=body.moneda,
            token_medio_pago=body.token_medio_pago,
        )
        resultado = app.state.servicio_cobro.cobrar(orden)
        return ResultadoCobroResponse(
            estado=resultado.estado,
            referencia=resultado.referencia,
            motivo=resultado.motivo,
            detalle=resultado.detalle,
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
