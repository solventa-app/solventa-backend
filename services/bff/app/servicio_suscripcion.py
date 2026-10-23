"""Aceptar una oferta y consultar la propuesta (HU-W03, KAN-209, CA-W03-01).

El BFF valida el consentimiento y el titular ANTES de pasarle la oferta a POLICY (regla #7); POLICY
vuelve a leer la oferta de RATING y guarda el precio de RATING, nunca uno que mande el cliente."""

import logging
from dataclasses import dataclass

from app.autenticacion_usuarios import UsuarioActual
from app.clientes import Clientes, ErrorServicio, PropuestaRechazada
from app.identificadores import id_opaco

logger = logging.getLogger("bff.suscripcion")

MENSAJE_OFERTA_NO_DISPONIBLE = "La oferta ya no está disponible. Vuelve a cotizar."
MENSAJE_OFERTA_PRELIMINAR = "Solo puedes aceptar una oferta completa."
MENSAJE_OFERTA_VENCIDA = "La oferta venció. Vuelve a cotizar."
MENSAJE_ACEPTACION_SIN_CONSENTIMIENTO = "No hay un consentimiento vigente para esta oferta."
MENSAJE_SUSCRIPCION_NO_DISPONIBLE = "No pudimos procesar tu suscripción. Intenta de nuevo."

# Motivos del contrato (`MotivoErrorAceptacion`) y su texto para el usuario.
_MENSAJE_POR_MOTIVO = {
    "OFERTA_NO_ENCONTRADA": MENSAJE_OFERTA_NO_DISPONIBLE,
    "OFERTA_PRELIMINAR": MENSAJE_OFERTA_PRELIMINAR,
    "OFERTA_VENCIDA": MENSAJE_OFERTA_VENCIDA,
    "CONSENTIMIENTO_NO_VIGENTE": MENSAJE_ACEPTACION_SIN_CONSENTIMIENTO,
}


@dataclass
class ErrorAceptacionDatos:
    mensaje: str
    puede_reintentar: bool
    motivo: str | None = None


def _error(motivo: str, puede_reintentar: bool = False) -> ErrorAceptacionDatos:
    return ErrorAceptacionDatos(_MENSAJE_POR_MOTIVO[motivo], puede_reintentar, motivo)


class ServicioSuscripcion:
    def __init__(self, clientes: Clientes) -> None:
        self._clientes = clientes

    async def aceptar(
        self, oferta_id: str, usuario: UsuarioActual | None
    ) -> dict | ErrorAceptacionDatos:
        """La propuesta tal como la entrega POLICY, o un `ErrorAceptacionDatos`. Nunca lanza por
        una falla de un servicio: se devuelve un error con reintento (CA-W03-01)."""
        # 1. La oferta tal como RATING la guardó: de ella salen el titular y el consentimiento.
        try:
            oferta = await self._clientes.rating.obtener_oferta(oferta_id)
        except ErrorServicio as error:
            logger.warning("RATING no disponible al aceptar error=%s", error)
            return ErrorAceptacionDatos(MENSAJE_SUSCRIPCION_NO_DISPONIBLE, puede_reintentar=True)
        if oferta is None:
            return _error("OFERTA_NO_ENCONTRADA")

        # 2. Consentimiento vigente del titular de la oferta, antes de seguir (regla #7).
        if not await self._consentimiento_vigente(oferta, usuario):
            logger.info("aceptación rechazada: sin consentimiento vigente")
            return _error("CONSENTIMIENTO_NO_VIGENTE")

        # 3. POLICY guarda la propuesta con el precio de RATING (idempotente por oferta).
        try:
            return await self._clientes.policy.crear_propuesta(
                oferta_id, usuario.id if usuario is not None else None
            )
        except PropuestaRechazada as rechazo:
            if rechazo.motivo not in _MENSAJE_POR_MOTIVO:
                logger.warning("POLICY rechazó la oferta con un motivo desconocido")
                return ErrorAceptacionDatos(
                    MENSAJE_SUSCRIPCION_NO_DISPONIBLE, puede_reintentar=True
                )
            return _error(rechazo.motivo)
        except ErrorServicio as error:
            logger.warning("POLICY no disponible oferta=%s error=%s", id_opaco(oferta_id), error)
            return ErrorAceptacionDatos(MENSAJE_SUSCRIPCION_NO_DISPONIBLE, puede_reintentar=True)

    async def _consentimiento_vigente(self, oferta: dict, usuario: UsuarioActual | None) -> bool:
        consentimiento_id = oferta.get("consentimientoId")
        if not consentimiento_id:
            return False
        try:
            consentimiento = await self._clientes.auth.obtener_consentimiento(consentimiento_id)
        except ErrorServicio as error:
            logger.warning("AUTH no disponible al aceptar error=%s", error)
            return False
        if consentimiento is None or not consentimiento.get("vigente"):
            return False
        titular = consentimiento.get("titular_id")
        if titular is not None and titular != oferta.get("clienteId"):
            return False
        # Un CLIENTE solo acepta lo que él mismo consintió; un ASESOR puede actuar por el cliente.
        actor = consentimiento.get("actor")
        if usuario is not None and usuario.rol == "CLIENTE" and actor is not None:
            return actor.get("usuario_id") == usuario.id
        return True

    async def obtener_propuesta(
        self, propuesta_id: str, usuario: UsuarioActual | None
    ) -> dict | None:
        """`None` si no existe o, para un CLIENTE, si la aceptó otra persona. Si POLICY no responde
        lanza `ErrorServicio`: el llamador no debe confundirlo con "no existe"."""
        propuesta = await self._clientes.policy.obtener_propuesta(propuesta_id)
        if propuesta is None:
            return None
        dueno = propuesta.get("actor_usuario_id")
        if usuario is not None and usuario.rol == "CLIENTE" and dueno not in (None, usuario.id):
            return None
        return propuesta
