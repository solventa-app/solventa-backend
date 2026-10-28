"""ACL Worker: capa anticorrupción hexagonal. Único punto de contacto con
proveedores externos (Open Finance, Open Data, pasarela de pagos, firma electrónica, KYC y
notificaciones) — ver `app/domain/puertos.py` y el README de este servicio.
"""

import base64
import binascii
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
import rq
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from app.adapters.autenticacion_servicios import autenticacion_para
from app.adapters.firma import AdaptadorFirma
from app.adapters.firma_alterna import AdaptadorFirmaAlterna
from app.adapters.identidad_didit import AdaptadorIdentidadDidit
from app.adapters.kyc import AdaptadorKyc
from app.adapters.notificaciones import AdaptadorNotificaciones
from app.adapters.open_data import AdaptadorOpenData
from app.adapters.open_finance import AdaptadorOpenFinance
from app.adapters.pasarela import AdaptadorPasarela
from app.adapters.pasarela_alterna import AdaptadorPasarelaAlterna
from app.adapters.tasas_cambio import AdaptadorTasasReferencia
from app.application.cache import CacheFuentes
from app.application.cache_identidad import CacheIdentidad
from app.application.circuitos import (
    NOMBRE_CIRCUITO_FIRMA,
    NOMBRE_CIRCUITO_IDENTIDAD,
    NOMBRE_CIRCUITO_KYC,
    NOMBRE_CIRCUITO_NOTIFICACIONES,
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
from app.application.servicio_identidad import DescargadorImagenes, ServicioIdentidad
from app.application.servicio_kyc import ServicioKyc
from app.application.servicio_notificaciones import ServicioNotificaciones
from app.application.servicio_pagos import ServicioCobro
from app.application.sonda import SondaRecuperacion
from app.domain.puertos import (
    Adjunto,
    ConsultaFuente,
    OrdenCobro,
    SolicitudFirma,
    SolicitudKyc,
    SolicitudNotificacion,
    SolicitudSesionPago,
)
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
logger = logging.getLogger("acl_worker")


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


class SolicitudIdentidadRequest(BaseModel):
    # Referencia opaca del onboarding; las URLs son las firmadas de descarga del almacenamiento.
    referencia: str = Field(min_length=1, max_length=100)
    anverso_url: str = Field(min_length=1, max_length=4096)
    rostro_url: str = Field(min_length=1, max_length=4096)
    reverso_url: str | None = Field(default=None, min_length=1, max_length=4096)


class DatosDocumentoResponse(BaseModel):
    nombres: str | None
    apellidos: str | None
    numero: str | None
    fecha_nacimiento: str | None
    pais: str | None
    tipo: str | None


class ResultadoIdentidadResponse(BaseModel):
    estado: str
    motivo: str | None
    datos: DatosDocumentoResponse | None
    documento_autentico: bool | None
    coincide_rostro: bool | None
    puntaje_rostro: float | None
    vivo: bool | None
    puntaje_vida: float | None
    alertas: list[str]


# Tipos de medio que pueden viajar como adjunto de un aviso, y cuántos.
TIPOS_DE_ADJUNTO = ("application/pdf",)
MAXIMO_ADJUNTOS = 3


class AdjuntoRequest(BaseModel):
    nombre: str
    tipo: str
    contenido_base64: str


class SolicitudNotificacionRequest(BaseModel):
    id_notificacion: str
    plantilla: str
    destinatario_id: str
    datos: dict[str, str] = {}
    # Datos personales y financieros (bloque 8, W03-16): solo viajan al proveedor. Sus reglas se
    # validan en la ruta y no con validadores de pydantic: un error de pydantic devuelve el cuerpo
    # de entrada, y ahí irían el correo y el PDF.
    correo: str | None = None
    telefono: str | None = None
    adjuntos: list[AdjuntoRequest] = []


class ResultadoNotificacionResponse(BaseModel):
    estado: str
    referencia: str | None
    motivo: str | None


class SolicitudSesionPagoRequest(BaseModel):
    id_intento_cobro: str
    monto: str
    moneda: str
    descripcion: str
    url_retorno: str
    idioma: str = "es"


class MedioPagoResponse(BaseModel):
    token: str  # token del proveedor PCI-DSS; nunca el número ni los últimos dígitos
    marca: str
    tipo: str


class SesionPagoResponse(BaseModel):
    estado: str
    sesion_id: str | None = None
    url_pago: str | None = None
    vence_en: str | None = None
    motivo: str | None = None
    medio: MedioPagoResponse | None = None
    referencia: str | None = None


class ResultadoSesionResponse(BaseModel):
    """`creada` (con la sesión; puede venir `rechazada` si la pasarela no aceptó la solicitud) o
    `no_disponible` (con el motivo)."""

    resultado: str
    sesion: SesionPagoResponse | None = None
    motivo: str | None = None


class ConsultaSesionResponse(BaseModel):
    """`encontrado` (con la sesión), `no_encontrado` o `desconocido`."""

    resultado: str
    sesion: SesionPagoResponse | None = None


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


def _validar_adjuntos(body: SolicitudNotificacionRequest, maximo_bytes: int) -> list[Adjunto]:
    """Los adjuntos ya validados, o un 422 cuyo mensaje nunca repite el contenido ni el correo."""
    if not body.adjuntos:
        return []
    if not body.correo:
        raise HTTPException(status_code=422, detail="Un adjunto solo se envía por correo.")
    if len(body.adjuntos) > MAXIMO_ADJUNTOS:
        raise HTTPException(status_code=422, detail=f"Máximo {MAXIMO_ADJUNTOS} adjuntos por aviso.")
    validados: list[Adjunto] = []
    for adjunto in body.adjuntos:
        if adjunto.tipo not in TIPOS_DE_ADJUNTO:
            raise HTTPException(status_code=422, detail="Tipo de adjunto no permitido.")
        try:
            contenido = base64.b64decode(adjunto.contenido_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(
                status_code=422, detail="El contenido del adjunto no es base64 válido."
            ) from error
        if not contenido:
            raise HTTPException(status_code=422, detail="El adjunto está vacío.")
        if len(contenido) > maximo_bytes:
            raise HTTPException(status_code=422, detail="El adjunto supera el tamaño permitido.")
        validados.append(Adjunto(adjunto.nombre, adjunto.tipo, adjunto.contenido_base64))
    return validados


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

        adaptador_identidad = AdaptadorIdentidadDidit(
            config.identidad_url,
            config.identidad_api_key,
            config.identidad_timeout_s,
            auth=(
                None
                if config.identidad_url.startswith("https://verification.didit.me")
                else autenticacion_para(config.autenticacion_servicios, "stub-kyc")
            ),
        )
        descargador_imagenes = DescargadorImagenes(
            config.identidad_origenes_imagenes,
            config.identidad_timeout_s,
            reescrituras=dict(config.identidad_reescrituras),
        )
        llave_cache_identidad = config.identidad_llave_cache
        if llave_cache_identidad is None and config.autenticacion_servicios == "ninguna":
            # Local y CI: una llave por proceso (la caché se pierde al reiniciar, como se espera).
            llave_cache_identidad = os.urandom(32)
        servicio_identidad = ServicioIdentidad(
            adaptador_identidad,
            fabrica,
            NOMBRE_CIRCUITO_IDENTIDAD,
            config.identidad_timeout_s,
            descargador_imagenes.descargar,
            cache=(
                CacheIdentidad(
                    cache_redis, llave_cache_identidad, config.identidad_cache_ttl_segundos
                )
                if llave_cache_identidad is not None
                else None
            ),
        )
        if llave_cache_identidad is None:
            logger.warning("IDENTIDAD_LLAVE_CACHE sin cargar: la caché de identidad está apagada")

        presupuesto_notificaciones_s = config.presupuesto_proveedor_s("notificaciones")
        adaptador_notificaciones = AdaptadorNotificaciones(
            config.notificaciones_url,
            presupuesto_notificaciones_s,
            auth=autenticacion_para(config.autenticacion_servicios, "stub-notificaciones"),
        )
        servicio_notificaciones = ServicioNotificaciones(
            adaptador_notificaciones,
            fabrica,
            NOMBRE_CIRCUITO_NOTIFICACIONES,
            presupuesto_notificaciones_s,
        )

        sonda = SondaRecuperacion(
            fabrica,
            sondeos={
                NOMBRE_CIRCUITO_OPEN_FINANCE: adaptador_open_finance.sondear,
                NOMBRE_CIRCUITO_OPEN_DATA: adaptador_open_data.sondear,
                NOMBRE_CIRCUITO_PASARELA: adaptador_pasarela.sondear,
                NOMBRE_CIRCUITO_FIRMA: adaptador_firma.sondear,
                NOMBRE_CIRCUITO_KYC: adaptador_kyc.sondear,
                NOMBRE_CIRCUITO_IDENTIDAD: adaptador_identidad.sondear,
                NOMBRE_CIRCUITO_NOTIFICACIONES: adaptador_notificaciones.sondear,
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
        app.state.servicio_identidad = servicio_identidad
        app.state.servicio_notificaciones = servicio_notificaciones

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
                adaptador_identidad.cerrar,
                descargador_imagenes.cerrar,
                adaptador_notificaciones.cerrar,
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
            NOMBRE_CIRCUITO_IDENTIDAD,
            NOMBRE_CIRCUITO_NOTIFICACIONES,
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

    def _sesion_response(sesion) -> SesionPagoResponse | None:
        if sesion is None:
            return None
        medio = sesion.medio
        return SesionPagoResponse(
            estado=sesion.estado,
            sesion_id=sesion.sesion_id,
            url_pago=sesion.url_pago,
            vence_en=sesion.vence_en,
            motivo=sesion.motivo,
            medio=MedioPagoResponse(token=medio.token, marca=medio.marca, tipo=medio.tipo)
            if medio
            else None,
            referencia=sesion.referencia,
        )

    @app.post("/pagos/sesiones", tags=["pagos"], response_model=ResultadoSesionResponse)
    def crear_sesion_pago(body: SolicitudSesionPagoRequest) -> ResultadoSesionResponse:
        """Sesión de la página de pago alojada (HU-W03, W03-28). Siempre 200 (regla #4)."""
        resultado = app.state.servicio_cobro.crear_sesion(
            SolicitudSesionPago(
                id_intento_cobro=body.id_intento_cobro,
                monto=body.monto,
                moneda=body.moneda,
                descripcion=body.descripcion,
                url_retorno=body.url_retorno,
                idioma=body.idioma,
            )
        )
        return ResultadoSesionResponse(
            resultado=resultado.resultado,
            sesion=_sesion_response(resultado.sesion),
            motivo=resultado.motivo,
        )

    @app.get("/pagos/sesiones/{sesion_id}", tags=["pagos"], response_model=ConsultaSesionResponse)
    def consultar_sesion_pago(sesion_id: str) -> ConsultaSesionResponse:
        """Confirmación de servidor a servidor al regresar de la pasarela (W03-28). Siempre 200."""
        consulta = app.state.servicio_cobro.consultar_sesion(sesion_id)
        return ConsultaSesionResponse(
            resultado=consulta.resultado, sesion=_sesion_response(consulta.sesion)
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

    @app.post("/identidad/verificar", tags=["identidad"], response_model=ResultadoIdentidadResponse)
    def verificar_identidad(body: SolicitudIdentidadRequest) -> ResultadoIdentidadResponse:
        """Verificación de identidad del onboarding móvil (HU-M01): documento, prueba de vida y
        rostro. La llama ONBOARDING en segundo plano. Siempre 200: si una imagen no se pudo bajar,
        el proveedor falla o el circuito está abierto, `pendiente` con su motivo (regla #4).
        La respuesta trae datos personales del documento: no se registra."""
        resultado = app.state.servicio_identidad.verificar(
            body.referencia, body.anverso_url, body.rostro_url, body.reverso_url
        )
        datos = resultado.datos
        return ResultadoIdentidadResponse(
            estado=resultado.estado,
            motivo=resultado.motivo,
            datos=DatosDocumentoResponse(**vars(datos)) if datos is not None else None,
            documento_autentico=resultado.documento_autentico,
            coincide_rostro=resultado.coincide_rostro,
            puntaje_rostro=resultado.puntaje_rostro,
            vivo=resultado.vivo,
            puntaje_vida=resultado.puntaje_vida,
            alertas=list(resultado.alertas),
        )

    @app.post(
        "/notificaciones/enviar",
        tags=["notificaciones"],
        response_model=ResultadoNotificacionResponse,
    )
    def enviar_notificacion(body: SolicitudNotificacionRequest) -> ResultadoNotificacionResponse:
        """Aviso al cliente (HU-W03). Con el proveedor caído o el circuito abierto responde 200
        `pendiente` con su motivo (regla #4) y POLICY lo reintenta. Idempotente por
        `id_notificacion`: repetirlo no manda dos avisos. Una solicitud mal armada (adjunto sin
        correo, de un tipo no permitido, que no es base64 o que pasa el tope) es un 422 con un
        mensaje que no repite el contenido: es un error de quien llama, no del proveedor."""
        adjuntos = _validar_adjuntos(body, config.adjunto_maximo_bytes)
        resultado = app.state.servicio_notificaciones.enviar(
            SolicitudNotificacion(
                id_notificacion=body.id_notificacion,
                plantilla=body.plantilla,
                destinatario_id=body.destinatario_id,
                datos=body.datos,
                correo=body.correo or None,
                telefono=body.telefono or None,
                adjuntos=adjuntos,
            )
        )
        return ResultadoNotificacionResponse(
            estado=resultado.estado, referencia=resultado.referencia, motivo=resultado.motivo
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
