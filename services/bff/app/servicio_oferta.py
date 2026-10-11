"""Orquestación de `solicitarOferta` (T-W01-7, CA-W01-01..06).

El BFF no calcula nada de negocio: secuencia AUTH → ACL Worker (5 fuentes en
paralelo) → RISK → RATING y traduce cualquier falla a `ErrorOferta` u oferta
preliminar (regla #4: nunca un 5xx crudo). Ver "Quién orquesta" en
`docs/arquitectura-backend.md`."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.clientes import Clientes, ErrorServicio, SolicitudInvalida
from app.identificadores import cliente_id_desde_documento, id_opaco

logger = logging.getLogger("bff.oferta")

# Mismos nombres que `FUENTES_SOPORTADAS` de services/acl-worker/app/adapters/open_*.py
# (copiados: los servicios no comparten código). Orden: Open Finance y luego Open Data.
FUENTES = (
    "cuentas-bancarias",
    "historial-crediticio",
    "afiliacion-pila",
    "camara-comercio",
    "antecedentes-judiciales",
)

MENSAJE_SIN_CONSENTIMIENTO = "No hay un consentimiento vigente para consultar tus datos."
MENSAJE_REINTENTAR = (
    "No pudimos calcular tu oferta en este momento. Intenta de nuevo o habla con un asesor."
)
MENSAJE_VALOR_INVALIDO = "El valor del crédito no es válido."


@dataclass
class ErrorOfertaDatos:
    mensaje: str
    puede_reintentar: bool


class ServicioOferta:
    def __init__(self, clientes: Clientes, cliente_id_clave: str) -> None:
        self._clientes = clientes
        self._clave = cliente_id_clave

    async def solicitar(
        self, consentimiento_id: str, documento_identidad: str, valor_credito: str
    ) -> dict | ErrorOfertaDatos:
        """Devuelve la oferta tal como la entrega RATING (ya en el camelCase del
        contrato) o un `ErrorOfertaDatos`. `plazoMeses` y `direccionInmueble` del
        contrato no los usa RATING todavía: no llegan aquí y nunca se loguean."""
        # 1. Consentimiento ANTES de consultar cualquier fuente (regla #7, CA-W01-01/02).
        try:
            consentimiento = await self._clientes.auth.obtener_consentimiento(consentimiento_id)
        except ErrorServicio as error:
            logger.warning("AUTH no disponible error=%s", error)
            return ErrorOfertaDatos(MENSAJE_REINTENTAR, puede_reintentar=True)
        if consentimiento is None or not consentimiento.get("vigente"):
            logger.info("oferta rechazada: sin consentimiento vigente")
            return ErrorOfertaDatos(MENSAJE_SIN_CONSENTIMIENTO, puede_reintentar=False)

        # 2. REWIRE: ver `cliente_id_desde_documento` (app/identificadores.py).
        cliente_id = cliente_id_desde_documento(documento_identidad, self._clave)

        # 3. Las 5 fuentes en paralelo. El ACL Worker ya degrada solo (caché o
        # valor por defecto); aquí solo se cubre que el ACL mismo no responda.
        fuentes = await asyncio.gather(
            *(self._consultar_fuente(cliente_id, consentimiento_id, f) for f in FUENTES)
        )

        # 4. RISK escribe el perfil y devuelve `operation_time` (D-02).
        try:
            perfil = await self._clientes.risk.registrar_perfil(cliente_id, list(fuentes))
        except ErrorServicio as error:
            logger.warning("RISK no disponible cliente=%s error=%s", id_opaco(cliente_id), error)
            return ErrorOfertaDatos(MENSAJE_REINTENTAR, puede_reintentar=True)

        # 5. RATING calcula la oferta leyendo el perfil con sesión causal.
        try:
            oferta = await self._clientes.rating.generar_oferta(
                cliente_id, perfil["operation_time"], valor_credito
            )
        except SolicitudInvalida:
            return ErrorOfertaDatos(MENSAJE_VALOR_INVALIDO, puede_reintentar=False)
        except ErrorServicio as error:
            logger.warning("RATING no disponible cliente=%s error=%s", id_opaco(cliente_id), error)
            return ErrorOfertaDatos(MENSAJE_REINTENTAR, puede_reintentar=True)

        logger.info(
            "oferta entregada cliente=%s tipo=%s fuentes_degradadas=%d",
            id_opaco(cliente_id),
            oferta.get("tipo"),
            sum(1 for f in fuentes if f.get("degradado") or f.get("de_cache")),
        )
        return oferta

    async def _consultar_fuente(self, cliente_id: str, consentimiento_id: str, fuente: str) -> dict:
        try:
            return await self._clientes.acl.consultar_fuente(cliente_id, consentimiento_id, fuente)
        except ErrorServicio as error:
            logger.warning("ACL no disponible fuente=%s error=%s", fuente, error)
            return {
                "fuente": fuente,
                "datos": {},
                "capturado_en": datetime.now(UTC).isoformat(),
                "de_cache": False,
                "degradado": True,
            }
