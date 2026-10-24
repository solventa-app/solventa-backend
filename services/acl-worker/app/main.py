"""ACL Worker: capa anticorrupción hexagonal. Único punto de contacto con
proveedores externos (Open Finance, Open Data, pasarela de pagos, firma electrónica, KYC) — ver
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

from app.adapters.autenticacion_servicios import autenticacion_para
from app.adapters.firma import AdaptadorFirma
from app.adapters.firma_alterna import AdaptadorFirmaAlterna
from app.adapters.kyc import AdaptadorKyc
from app.adapters.open_data import AdaptadorOpenData
from app.adapters.open_finance import AdaptadorOpenFinance
from app.adapters.pasarela import AdaptadorPasarela
from app.adapters.pasarela_alterna import AdaptadorPasarelaAlterna
from app.adapters.tasas_cambio import AdaptadorTasasReferencia
from app.application.cache import CacheFuentes
from app.application.circuitos import (
    NOMBRE_CIRCUITO_FIRMA,
    NOMBRE_CIRCUITO_KYC,
    NOMBRE_CIRCUITO_OPEN_DATA,
    NOMBRE_CIRCUITO_OPEN_FINANCE,
    NOMBRE_CIRCUITO_PASARELA,
    construir_fabrica,
)
from app.application.cola_tareas import ColaReconciliacionCloudTasks
from app.application.configuracion import Configuracion
from app.application.fuentes_config import (
    fuentes_open_data,
    fuentes_open_finance,
)
from app.application.reconciliacion import (
    NOMBRE_COLA,
    ColaReconciliacionRQ,
    MarcaDedupRedis,
    ProductorReconciliacion,
)
from app.application.servicio_firma import ServicioFirma
from app.application.servicio_fuentes import ServicioConsultaFuentes
from app.application.servicio_kyc import ServicioKyc
from app.application.servicio_pagos import ServicioCobro
from app.application.sonda import SondaRecuperacion
from app.domain.puertos import ConsultaFuente, OrdenCobro, SolicitudFirma, SolicitudKyc
from app.observabilidad import configurar_trazas

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

configurar_trazas("acl-worker", instrumentos=("httpx", "redis"))


class ConsultaFuenteRequest(BaseModel):
    cliente_id: str
    consentimiento_id: str
    fuente: str
    finalidad: str = "perfilamiento"
    forzar_proveedor: bool = False


class InvalidarCacheRequest(BaseModel):
    cliente_id: str


class InvalidarCacheResponse(BaseModel):
    invalidado: bool


class DatosFuenteResponse(BaseModel):
    fuente: str
    datos: dict
    capturado_en: str
    de_cache: bool
    degradado: bool


class OrdenCobroRequest(BaseModel):
    id_intento_cobro: str
    poliza_id: str
    monto: str
    moneda: str
    token_medio_pago: str


class ResultadoCobroResponse(BaseModel):
    estado: str
    referencia: str | None
    motivo: str | None
    detalle: dict


class SolicitudFirmaRequest(BaseModel):
    id_firma: str
    documento_hash: str
    version_documento: str
    firmante_id: str


class ResultadoFirmaResponse(BaseModel):
    estado: str
    referencia: str | None
    firmada_en: str | None
    motivo: str | None


class SolicitudKycRequest(BaseModel):
    id_verificacion: str
    cliente_id: str


class ResultadoKycResponse(BaseModel):
    estado: str
    referencia: str | None
    motivo: str | None
    verificada_en: str | None


class ConsultaCobroResponse(BaseModel):
    """Conciliación: `encontrado` (con el cobro), `no_encontrado` o `desconocido`."""

    resultado: str
    estado: str | None = None
    referencia: str | None = None
    motivo: str | None = None


class TasasCambioResponse(BaseModel):
    base: str
    cop_por_unidad: dict[str, str]
    fecha: str
    fuente: str


def _construir_servicios_fuentes(
    config: Configuracion,
    fabrica,
    cache_redis,
    productor_reconciliacion,
    cache: CacheFuentes | None = None,
) -> tuple[dict[str, ServicioConsultaFuentes], AdaptadorOpenFinance, AdaptadorOpenData]:
    modo = config.autenticacion_servicios
    fuentes_of = fuentes_open_finance(config.fuentes_config_path)
    fuentes_od = fuentes_open_data(config.fuentes_config_path)
    presupuesto_of_s = config.presupuesto_proveedor_s("open-finance")
    presupuesto_od_s = config.presupuesto_proveedor_s("open-data")

    adaptador_open_finance = AdaptadorOpenFinance(
        config.open_finance_url,
        presupuesto_of_s,
        auth=autenticacion_para(modo, "stub-open-finance"),
        fuentes=fuentes_of,
    )
    adaptador_open_data = AdaptadorOpenData(
        config.open_data_url,
        presupuesto_od_s,
        auth=autenticacion_para(modo, "stub-open-data"),
        fuentes=fuentes_od,
    )
    cache_fuentes = cache or CacheFuentes(cache_redis, config.cache_ttl_segundos)

    servicio_open_finance = ServicioConsultaFuentes(
        adaptador_open_finance,
        fabrica,
        NOMBRE_CIRCUITO_OPEN_FINANCE,
        cache_fuentes,
        presupuesto_of_s,
        productor_reconciliacion,
        presupuesto_con_cache_s=min(presupuesto_of_s, config.presupuesto_con_cache_s),
        servir_cache_en_ttl=config.cache_servir_en_ttl,
    )
    servicio_open_data = ServicioConsultaFuentes(
        adaptador_open_data,
        fabrica,
        NOMBRE_CIRCUITO_OPEN_DATA,
        cache_fuentes,
        presupuesto_od_s,
        productor_reconciliacion,
        presupuesto_con_cache_s=min(presupuesto_od_s, config.presupuesto_con_cache_s),
        servir_cache_en_ttl=config.cache_servir_en_ttl,
    )

    servicios_por_fuente = {fuente: servicio_open_finance for fuente in fuentes_of}
    servicios_por_fuente.update({fuente: servicio_open_data for fuente in fuentes_od})

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
        # Con DISPARADOR=externo (Cloud Run) la reconciliación va por Cloud Tasks al consolidador;
        # la marca de dedup sigue en Redis. En local y CI sigue la cola `rq`.
        if config.disparador == "externo":
            cola_reconciliacion = ColaReconciliacionCloudTasks(
                config.tareas_proyecto,
                config.tareas_region,
                config.tareas_cola_reconciliacion,
                config.consolidador_url,
                config.tareas_cuenta,
            )
        else:
            cola_rq = rq.Queue(NOMBRE_COLA, connection=redis_cola)
            cola_reconciliacion = ColaReconciliacionRQ(cola_rq, REINTENTOS_RECONCILIACION)
        productor_reconciliacion = ProductorReconciliacion(
            cola=cola_reconciliacion,
            dedup=MarcaDedupRedis(redis_cola),
        )

        cache_fuentes = CacheFuentes(cache_redis, config.cache_ttl_segundos)
        servicios_por_fuente, adaptador_open_finance, adaptador_open_data = (
            _construir_servicios_fuentes(
                config, fabrica, cache_redis, productor_reconciliacion, cache=cache_fuentes
            )
        )

        presupuesto_pasarela_s = config.presupuesto_proveedor_s("pasarela")
        # EC031: cambiar de proveedor de pagos es elegir otro adaptador; nada más cambia.
        clase_pasarela = (
            AdaptadorPasarelaAlterna
            if config.proveedor_pasarela == "alterna"
            else AdaptadorPasarela
        )
        adaptador_pasarela = clase_pasarela(
            config.pasarela_url,
            presupuesto_pasarela_s,
            auth=autenticacion_para(config.autenticacion_servicios, "stub-pasarela"),
        )
        servicio_cobro = ServicioCobro(
            adaptador_pasarela, fabrica, NOMBRE_CIRCUITO_PASARELA, presupuesto_pasarela_s
        )

        presupuesto_firma_s = config.presupuesto_proveedor_s("firma")
        # EC031: cambiar de proveedor de firma es elegir otro adaptador; nada más cambia.
        clase_firma = (
            AdaptadorFirmaAlterna if config.proveedor_firma == "alterna" else AdaptadorFirma
        )
        adaptador_firma = clase_firma(
            config.firma_url,
            presupuesto_firma_s,
            auth=autenticacion_para(config.autenticacion_servicios, "stub-firma"),
        )
        servicio_firma = ServicioFirma(
            adaptador_firma, fabrica, NOMBRE_CIRCUITO_FIRMA, presupuesto_firma_s
        )

        presupuesto_kyc_s = config.presupuesto_proveedor_s("kyc")
        adaptador_kyc = AdaptadorKyc(
            config.kyc_url,
            presupuesto_kyc_s,
            auth=autenticacion_para(config.autenticacion_servicios, "stub-kyc"),
        )
        servicio_kyc = ServicioKyc(adaptador_kyc, fabrica, NOMBRE_CIRCUITO_KYC, presupuesto_kyc_s)

        sonda = SondaRecuperacion(
            fabrica,
            sondeos={
                NOMBRE_CIRCUITO_OPEN_FINANCE: adaptador_open_finance.sondear,
                NOMBRE_CIRCUITO_OPEN_DATA: adaptador_open_data.sondear,
                NOMBRE_CIRCUITO_PASARELA: adaptador_pasarela.sondear,
                NOMBRE_CIRCUITO_FIRMA: adaptador_firma.sondear,
                NOMBRE_CIRCUITO_KYC: adaptador_kyc.sondear,
            },
            intervalo_s=config.sonda_intervalo_segundos,
        )
        # Con DISPARADOR=externo no hay bucle: Cloud Scheduler llama a POST /sonda/ejecutar.
        if config.disparador == "bucle":
            sonda.iniciar()

        app.state.sonda = sonda
        app.state.fabrica_circuitos = fabrica
        app.state.servicios_por_fuente = servicios_por_fuente
        app.state.cache_fuentes = cache_fuentes
        app.state.servicio_cobro = servicio_cobro
        app.state.servicio_firma = servicio_firma
        app.state.servicio_kyc = servicio_kyc

        try:
            yield
        finally:
            await sonda.detener()
            cierres = (
                adaptador_open_finance.cerrar,
                adaptador_open_data.cerrar,
                adaptador_pasarela.cerrar,
                adaptador_firma.cerrar,
                adaptador_kyc.cerrar,
            )
            for cerrar in cierres:
                cerrar()
            cache_redis.close()
            redis_cola.close()

    app = FastAPI(title="solventa-acl-worker", version="0.1.0", lifespan=lifespan)
    # No depende de Redis ni de un proveedor externo: se crea aquí y no en el lifespan.
    tasas_cambio = AdaptadorTasasReferencia()

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
            NOMBRE_CIRCUITO_FIRMA,
            NOMBRE_CIRCUITO_KYC,
        )
        return {nombre: fabrica.get_breaker(nombre).context.state for nombre in nombres}

    @app.post("/sonda/ejecutar", tags=["operacion"])
    async def ejecutar_sonda() -> dict[str, str]:
        """La llama Cloud Scheduler (`DISPARADOR=externo`): una pasada de la sonda de recuperación,
        fuera del camino síncrono del usuario (regla #5). Es privada (IAM de Cloud Run) y
        devuelve el estado de cada circuito después de sondear. Nota: el estado de los circuitos
        vive en la memoria de cada instancia, así que sondea los de la instancia que recibe la
        llamada."""
        return await app.state.sonda.una_pasada()

    @app.post("/fuentes/consultar", tags=["fuentes"], response_model=DatosFuenteResponse)
    def consultar_fuente(body: ConsultaFuenteRequest) -> DatosFuenteResponse:
        servicio = app.state.servicios_por_fuente.get(body.fuente)
        if servicio is None:
            raise HTTPException(status_code=404, detail=f"fuente desconocida: {body.fuente}")

        consulta = ConsultaFuente(
            cliente_id=body.cliente_id,
            consentimiento_id=body.consentimiento_id,
            fuente=body.fuente,
            finalidad=body.finalidad,
            forzar_proveedor=body.forzar_proveedor,
        )
        datos = servicio.consultar(consulta)
        return DatosFuenteResponse(
            fuente=datos.fuente,
            datos=datos.datos,
            capturado_en=datos.capturado_en.isoformat(),
            de_cache=datos.de_cache,
            degradado=datos.degradado,
        )

    @app.post(
        "/fuentes/invalidar-cache",
        tags=["fuentes"],
        response_model=InvalidarCacheResponse,
    )
    def invalidar_cache(body: InvalidarCacheRequest) -> InvalidarCacheResponse:
        """Invalida en la caché todas las fuentes asociadas a `cliente_id` al
        revocar el consentimiento (CA-W01-08). El identificador nunca viaja en
        la URL (regla de arquitectura #6)."""
        fuentes = tuple(app.state.servicios_por_fuente.keys())
        app.state.cache_fuentes.invalidar_cliente(body.cliente_id, fuentes)
        return InvalidarCacheResponse(invalidado=True)

    @app.post("/pagos/cobrar", tags=["pagos"], response_model=ResultadoCobroResponse)
    def cobrar(body: OrdenCobroRequest) -> ResultadoCobroResponse:
        orden = OrdenCobro(
            id_intento_cobro=body.id_intento_cobro,
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

    @app.get(
        "/pagos/cobros/{referencia_comercio}",
        tags=["pagos"],
        response_model=ConsultaCobroResponse,
    )
    def consultar_cobro(referencia_comercio: str) -> ConsultaCobroResponse:
        """Conciliación (KAN-28). Siempre 200: si la pasarela no responde, `desconocido`
        (regla #4: nunca un 5xx por el proveedor)."""
        consulta = app.state.servicio_cobro.consultar(referencia_comercio)
        cobro = consulta.cobro
        return ConsultaCobroResponse(
            resultado=consulta.resultado,
            estado=cobro.estado if cobro else None,
            referencia=cobro.referencia if cobro else None,
            motivo=cobro.motivo if cobro else None,
        )

    @app.post("/firma/firmar", tags=["firma"], response_model=ResultadoFirmaResponse)
    def firmar(body: SolicitudFirmaRequest) -> ResultadoFirmaResponse:
        """Firma electrónica del documento de la póliza (HU-W03). Recibe solo el hash. Siempre 200:
        si el proveedor no responde o el circuito está abierto, `pendiente` con su motivo (regla
        #4), y POLICY ofrece reintentar. Repetir el mismo `id_firma` devuelve la misma evidencia."""
        resultado = app.state.servicio_firma.firmar(
            SolicitudFirma(
                id_firma=body.id_firma,
                documento_hash=body.documento_hash,
                version_documento=body.version_documento,
                firmante_id=body.firmante_id,
            )
        )
        return ResultadoFirmaResponse(
            estado=resultado.estado,
            referencia=resultado.referencia,
            firmada_en=resultado.firmada_en,
            motivo=resultado.motivo,
        )

    @app.post("/kyc/verificar", tags=["kyc"], response_model=ResultadoKycResponse)
    def verificar_kyc(body: SolicitudKycRequest) -> ResultadoKycResponse:
        """Verificación de identidad para decidir la suscripción (HU-W03). Siempre 200: si el
        proveedor no responde o el circuito está abierto, `pendiente` con su motivo (regla #4) y
        quien decide manda la propuesta a revisión manual. Idempotente por `id_verificacion`."""
        resultado = app.state.servicio_kyc.verificar(
            SolicitudKyc(id_verificacion=body.id_verificacion, cliente_id=body.cliente_id)
        )
        return ResultadoKycResponse(
            estado=resultado.estado,
            referencia=resultado.referencia,
            motivo=resultado.motivo,
            verificada_en=resultado.verificada_en,
        )

    @app.get("/tasas-cambio", tags=["tasas"], response_model=TasasCambioResponse)
    def tasas() -> TasasCambioResponse:
        """KAN-209: cuántos COP vale una unidad de cada moneda, para que el BFF muestre los
        montos en la moneda que el usuario eligió. Texto decimal, nunca float."""
        tabla = tasas_cambio.tasas()
        return TasasCambioResponse(
            base="COP", cop_por_unidad=tabla.cop_por_unidad, fecha=tabla.fecha, fuente=tabla.fuente
        )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)
    return app


app = create_app()
