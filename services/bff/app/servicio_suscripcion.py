"""Aceptar una oferta y consultar la propuesta (HU-W03, KAN-209, CA-W03-01).

El BFF valida el consentimiento y el titular ANTES de pasarle la oferta a POLICY (regla #7); POLICY
vuelve a leer la oferta de RATING y guarda el precio de RATING, nunca uno que mande el cliente."""

import logging
from dataclasses import dataclass

from app.autenticacion_usuarios import UsuarioActual
from app.clientes import Clientes, ErrorServicio, FirmaRechazada, PropuestaRechazada
from app.identificadores import id_opaco

logger = logging.getLogger("bff.suscripcion")

MENSAJE_OFERTA_NO_DISPONIBLE = "La oferta ya no está disponible. Vuelve a cotizar."
MENSAJE_OFERTA_PRELIMINAR = "Esta oferta es preliminar y no se puede aceptar. Vuelve a calcularla."
MENSAJE_OFERTA_VENCIDA = "La oferta venció. Vuelve a cotizar."
MENSAJE_ACEPTACION_SIN_CONSENTIMIENTO = "No hay un consentimiento vigente para esta oferta."
MENSAJE_SUSCRIPCION_NO_DISPONIBLE = "No pudimos procesar tu suscripción. Intenta de nuevo."

MENSAJE_DOCUMENTO_NO_DISPONIBLE = "No pudimos generar el documento. Intenta de nuevo."
MENSAJE_COBRO_NO_AUTORIZADO = "Debes autorizar el cobro de la prima para firmar."
MENSAJE_DOCUMENTO_DESACTUALIZADO = "El documento cambió. Revísalo de nuevo antes de firmar."
MENSAJE_PROPUESTA_NO_FIRMABLE = "Esta propuesta no se puede firmar."
MENSAJE_FIRMA_NO_DISPONIBLE = "No pudimos registrar tu firma. Intenta de nuevo."

# Motivos de `MotivoErrorAceptacion`: texto para el usuario y si hay algo que reintentar. Una oferta
# `PRELIMINAR` se resuelve recalculándola, es decir, volviendo a cotizar (D-11, CA-W03-01);
# repetir la misma aceptación no la cambia.
_ACEPTACION_POR_MOTIVO = {
    "OFERTA_NO_ENCONTRADA": (MENSAJE_OFERTA_NO_DISPONIBLE, False),
    "OFERTA_PRELIMINAR": (MENSAJE_OFERTA_PRELIMINAR, True),
    "OFERTA_VENCIDA": (MENSAJE_OFERTA_VENCIDA, False),
    "CONSENTIMIENTO_NO_VIGENTE": (MENSAJE_ACEPTACION_SIN_CONSENTIMIENTO, False),
}


# Motivos de `MotivoErrorFirma`: texto para el usuario y si tiene sentido volver a intentarlo.
_FIRMA_POR_MOTIVO = {
    "COBRO_NO_AUTORIZADO": (MENSAJE_COBRO_NO_AUTORIZADO, True),
    "VERSION_DOCUMENTO_DESACTUALIZADA": (MENSAJE_DOCUMENTO_DESACTUALIZADO, True),
    "PROPUESTA_NO_FIRMABLE": (MENSAJE_PROPUESTA_NO_FIRMABLE, False),
    "SERVICIO_FIRMA_NO_DISPONIBLE": (MENSAJE_FIRMA_NO_DISPONIBLE, True),
}


# Qué le decimos al cliente cuando la propuesta no avanza (CA-W03-06). Son categorías: el código
# interno de UNDER (`RIESGO_ALTO`, `KYC_RECHAZADO`...) dice cómo se evalúa a la persona y no sale
# del BFF.
# Los textos son una propuesta (decisión W03-15): viven aquí, en un solo lugar, para ajustarlos.
MENSAJE_PAGO_FONDOS_INSUFICIENTES = (
    "No pudimos cobrar tu primera cuota porque la cuenta no tiene fondos suficientes. "
    "Habla con un asesor para resolverlo."
)
MENSAJE_PAGO_RECHAZADO = "No pudimos cobrar tu primera cuota. Habla con un asesor para resolverlo."
MENSAJE_PAGO_EN_PROCESO = (
    "Tu pago está en proceso. Emitiremos tu póliza cuando se confirme y te avisaremos."
)
MENSAJE_REVISION_MANUAL = "Estamos revisando tu solicitud. Te avisamos cuando esté lista."
MENSAJE_NO_APROBADA = (
    "No pudimos aprobar tu solicitud. Habla con un asesor para conocer tus opciones."
)

# Código con el que la pasarela rechaza por falta de fondos; cualquier otro rechazo es genérico.
MOTIVO_PASARELA_FONDOS_INSUFICIENTES = "fondos_insuficientes"


@dataclass(frozen=True)
class ExplicacionEstado:
    motivo: str  # un valor de `MotivoEstadoPropuesta`
    mensaje: str
    accion: str  # un valor de `AccionSugerida`


def explicar_estado(propuesta: dict) -> ExplicacionEstado | None:
    """Qué le pasa a la propuesta y qué hacer, o `None` si no hay nada que explicar (aceptada,
    en emisión o emitida). Un motivo desconocido cae en la categoría general del estado: nunca
    falla ni deja pasar un código interno."""
    estado = propuesta.get("estado")
    if estado == "PAGO_RECHAZADO":
        if propuesta.get("motivo_estado") == MOTIVO_PASARELA_FONDOS_INSUFICIENTES:
            return ExplicacionEstado(
                "PAGO_FONDOS_INSUFICIENTES", MENSAJE_PAGO_FONDOS_INSUFICIENTES, "CONTACTAR_ASESOR"
            )
        return ExplicacionEstado("PAGO_RECHAZADO", MENSAJE_PAGO_RECHAZADO, "CONTACTAR_ASESOR")
    if estado == "PENDIENTE_PAGO":
        return ExplicacionEstado("PAGO_EN_PROCESO", MENSAJE_PAGO_EN_PROCESO, "ESPERAR")
    if estado == "EN_REVISION":
        return ExplicacionEstado("REVISION_MANUAL", MENSAJE_REVISION_MANUAL, "ESPERAR")
    if estado == "NO_EMITIDA":
        return ExplicacionEstado("NO_APROBADA", MENSAJE_NO_APROBADA, "CONTACTAR_ASESOR")
    return None


@dataclass
class ErrorFirmaDatos:
    mensaje: str
    puede_reintentar: bool
    motivo: str


def _error_firma(motivo: str) -> ErrorFirmaDatos:
    mensaje, puede_reintentar = _FIRMA_POR_MOTIVO[motivo]
    return ErrorFirmaDatos(mensaje, puede_reintentar, motivo)


@dataclass
class ErrorAceptacionDatos:
    mensaje: str
    puede_reintentar: bool
    motivo: str | None = None


def _error(motivo: str) -> ErrorAceptacionDatos:
    mensaje, puede_reintentar = _ACEPTACION_POR_MOTIVO[motivo]
    return ErrorAceptacionDatos(mensaje, puede_reintentar, motivo)


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
            if rechazo.motivo not in _ACEPTACION_POR_MOTIVO:
                logger.warning("POLICY rechazó la oferta con un motivo desconocido")
                return ErrorAceptacionDatos(
                    MENSAJE_SUSCRIPCION_NO_DISPONIBLE, puede_reintentar=True
                )
            return _error(rechazo.motivo)
        except ErrorServicio as error:
            logger.warning("POLICY no disponible oferta=%s error=%s", id_opaco(oferta_id), error)
            return ErrorAceptacionDatos(MENSAJE_SUSCRIPCION_NO_DISPONIBLE, puede_reintentar=True)

    async def obtener_oferta(self, oferta_id: str, usuario: UsuarioActual | None) -> dict | None:
        """La oferta que RATING guardó, con la misma validación de dueño que `aceptar`: `None` si
        venció, no existe, o no es de este cliente o ya no tiene consentimiento vigente. Si
        RATING o AUTH no responden lanza `ErrorServicio`: el llamador no debe confundirlo con
        "no existe"."""
        oferta = await self._clientes.rating.obtener_oferta(oferta_id)
        if oferta is None:
            return None
        if not await self._es_titular_con_consentimiento(oferta, usuario):
            return None
        return oferta

    async def _consentimiento_vigente(self, oferta: dict, usuario: UsuarioActual | None) -> bool:
        try:
            return await self._es_titular_con_consentimiento(oferta, usuario)
        except ErrorServicio as error:
            logger.warning("AUTH no disponible al aceptar error=%s", error)
            return False

    async def _es_titular_con_consentimiento(
        self, oferta: dict, usuario: UsuarioActual | None
    ) -> bool:
        """Lanza `ErrorServicio` si AUTH no responde."""
        consentimiento_id = oferta.get("consentimientoId")
        if not consentimiento_id:
            return False
        consentimiento = await self._clientes.auth.obtener_consentimiento(consentimiento_id)
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

    async def documento_firma(
        self, propuesta_id: str, usuario: UsuarioActual | None
    ) -> dict | None:
        """El documento a firmar, o `None` si la propuesta no existe, no es de este cliente o no se
        puede armar. Si POLICY no responde lanza `ErrorServicio`."""
        if await self.obtener_propuesta(propuesta_id, usuario) is None:
            return None
        return await self._clientes.policy.documento_firma(propuesta_id)

    async def firmar(
        self,
        propuesta_id: str,
        autoriza_cobro: bool,
        version_documento: str,
        usuario: UsuarioActual,
    ) -> dict | ErrorFirmaDatos:
        """La evidencia tal como la entrega POLICY, o un `ErrorFirmaDatos`. Solo el CLIENTE titular
        firma (no repudio): se exige sesión y que la propuesta sea suya. Nunca lanza por una falla
        de un servicio: se devuelve un error con reintento."""
        try:
            propuesta = await self.obtener_propuesta(propuesta_id, usuario)
        except ErrorServicio as error:
            logger.warning("POLICY no disponible al firmar error=%s", error)
            return _error_firma("SERVICIO_FIRMA_NO_DISPONIBLE")
        if propuesta is None:
            return _error_firma("PROPUESTA_NO_FIRMABLE")
        try:
            return await self._clientes.policy.firmar_propuesta(
                propuesta_id, autoriza_cobro, version_documento, usuario.id
            )
        except FirmaRechazada as rechazo:
            if rechazo.motivo not in _FIRMA_POR_MOTIVO:
                logger.warning("POLICY rechazó la firma con un motivo desconocido")
                return _error_firma("SERVICIO_FIRMA_NO_DISPONIBLE")
            return _error_firma(rechazo.motivo)
        except ErrorServicio as error:
            logger.warning("POLICY no disponible al firmar error=%s", error)
            return _error_firma("SERVICIO_FIRMA_NO_DISPONIBLE")

    async def reconciliar_cobro(self, poliza_id: str) -> None:
        """El back-office acaba de reintentar el cobro de `poliza_id`: se lo avisa a POLICY para
        que, si es el primer cobro de una propuesta y ya se cobró, emita la póliza (W03-14).
        De mejor esfuerzo: si POLICY no responde, el reintento del back-office no falla; el barrido
        de reconciliación la retoma. Para un cobro que no es de W03, POLICY no lo conoce y no pasa
        nada."""
        try:
            await self._clientes.policy.reconciliar_cobro(poliza_id)
        except ErrorServicio as error:
            logger.warning(
                "POLICY no pudo reconciliar el cobro poliza=%s error=%s", id_opaco(poliza_id), error
            )

    async def obtener_poliza(self, poliza_id: str, usuario: UsuarioActual | None) -> dict | None:
        """La póliza, o `None` si no existe o, para un CLIENTE, si la aceptó otra persona. Si POLICY
        no responde lanza `ErrorServicio`: el llamador no debe confundirlo con "no existe"."""
        poliza = await self._clientes.policy.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        dueno = poliza.get("actor_usuario_id")
        if usuario is not None and usuario.rol == "CLIENTE" and dueno not in (None, usuario.id):
            return None
        return poliza

    async def documento_poliza(
        self, poliza_id: str, usuario: UsuarioActual | None
    ) -> tuple[str, bytes] | None:
        """El PDF de la póliza y su número, o `None` si no existe o, para un CLIENTE, es de otra
        persona. Si POLICY no responde lanza `ErrorServicio`.

        El nombre del asegurado (el CLIENTE que firmó) se pide a AUTH solo para ponerlo en el
        documento: no se guarda ni se loguea. Si AUTH no responde, el documento sale igual con la
        referencia opaca del titular: una falla de AUTH no debe impedir descargar la póliza."""
        poliza = await self.obtener_poliza(poliza_id, usuario)
        if poliza is None:
            return None
        asegurado = await self._nombre_del_asegurado(poliza.get("firmante_id"))
        pdf = await self._clientes.policy.generar_documento(poliza_id, asegurado)
        return (poliza["numero"], pdf) if pdf is not None else None

    async def _nombre_del_asegurado(self, firmante_id: str | None) -> str | None:
        if not firmante_id:
            return None
        try:
            nombres = await self._clientes.auth.nombres_de_clientes([firmante_id])
        except ErrorServicio:
            logger.warning("AUTH no disponible al armar el documento")
            return None
        return nombres.get(firmante_id)
