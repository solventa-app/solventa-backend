"""Firma y pago con el asesor o en la sesión del CLIENTE (HU-W03, bloque 9; F-B6).

El BFF no decide nada de negocio: autoriza (rol, dueño de la propuesta y, en la página del enlace,
**token del enlace o titular de la sesión**, W03-33), llama a POLICY y traduce cada rechazo a un
error del contrato con su mensaje. Nunca lanza por una falla de un servicio: devuelve un error con
reintento (regla #4). Ni el token, ni el código, ni el contacto, ni el documento se loguean."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.autenticacion_usuarios import UsuarioActual
from app.clientes import Acceso, Clientes, ErrorServicio, FlujoRechazado
from app.identificadores import cliente_id_desde_documento, id_opaco

logger = logging.getLogger("bff.firma_pago")

MENSAJE_FLUJO_NO_DISPONIBLE = "No pudimos procesar la solicitud. Intenta de nuevo."
MENSAJE_PROPUESTA_NO_ENCONTRADA = "Propuesta no encontrada."
MENSAJE_PROPUESTA_NO_VALIDA = "La propuesta no está en un estado que permita esta operación."
MENSAJE_CODIGO_INVALIDO = "El código no es correcto. Revísalo e intenta de nuevo."
MENSAJE_CODIGO_VENCIDO = "El código venció. Pide uno nuevo."
MENSAJE_INTENTOS_AGOTADOS = "Agotaste los intentos. Pide un código nuevo."
MENSAJE_DATOS_INVALIDOS = "Revisa los datos e intenta de nuevo."
MENSAJE_ENLACE_VENCIDO = "El enlace venció. Pide a tu asesor que te envíe uno nuevo."
MENSAJE_IMAGEN_INVALIDA = "La imagen de la firma no es válida. Sube otra."
MENSAJE_COBRO_NO_AUTORIZADO = "Debes autorizar el cobro de la prima para firmar."
MENSAJE_DOCUMENTO_DESACTUALIZADO = "El documento cambió. Revísalo de nuevo antes de firmar."
MENSAJE_PAGO_RECHAZADO_TARJETA = "La pasarela rechazó el pago. Prueba con otra tarjeta."
MENSAJE_PAGO_FONDOS = "La tarjeta no tiene fondos suficientes. Prueba con otra."
MENSAJE_PAGO_TARJETA_VENCIDA = "La tarjeta está vencida. Prueba con otra."
MENSAJE_PASARELA_NO_DISPONIBLE = "La pasarela no responde. Intenta de nuevo en un momento."
MENSAJE_REQUIERE_OTRA_TARJETA = "No pudimos cobrar tras varios intentos. Usa otra tarjeta."
MENSAJE_SESION_VENCIDA = "La sesión de pago venció. Empieza de nuevo."
MENSAJE_PAGO_PENDIENTE = "Aún no recibimos tu pago. Intenta de nuevo en unos segundos."
MENSAJE_POLIZA_YA_EMITIDA = "Tu póliza ya fue emitida."
MENSAJE_DOCUMENTO_NO_COINCIDE = "El documento no coincide con el de tu cuenta."
MENSAJE_PROPUESTAS_NO_DISPONIBLES = "No pudimos consultar las propuestas. Intenta de nuevo."
MENSAJE_AVISOS_NO_DISPONIBLES = "No pudimos abrir los avisos. Intenta de nuevo."

# Motivo de POLICY -> (motivo del contrato, mensaje, si tiene sentido reintentar).
_POR_MOTIVO: dict[str, tuple[str, str, bool]] = {
    "PROPUESTA_NO_ENCONTRADA": ("PROPUESTA_NO_ENCONTRADA", MENSAJE_PROPUESTA_NO_ENCONTRADA, False),
    "PROPUESTA_NO_VALIDA": ("PROPUESTA_NO_VALIDA", MENSAJE_PROPUESTA_NO_VALIDA, False),
    "CODIGO_INVALIDO": ("CODIGO_INVALIDO", MENSAJE_CODIGO_INVALIDO, True),
    "CODIGO_VENCIDO": ("CODIGO_VENCIDO", MENSAJE_CODIGO_VENCIDO, True),
    "INTENTOS_AGOTADOS": ("INTENTOS_AGOTADOS", MENSAJE_INTENTOS_AGOTADOS, True),
    "DATOS_INVALIDOS": ("DATOS_INVALIDOS", MENSAJE_DATOS_INVALIDOS, False),
    "ENLACE_VENCIDO": ("ENLACE_VENCIDO", MENSAJE_ENLACE_VENCIDO, False),
    "IMAGEN_INVALIDA": ("IMAGEN_INVALIDA", MENSAJE_IMAGEN_INVALIDA, True),
    "COBRO_NO_AUTORIZADO": ("COBRO_NO_AUTORIZADO", MENSAJE_COBRO_NO_AUTORIZADO, True),
    "VERSION_DOCUMENTO_DESACTUALIZADA": (
        "VERSION_DOCUMENTO_DESACTUALIZADA",
        MENSAJE_DOCUMENTO_DESACTUALIZADO,
        True,
    ),
}

# Motivos de la sesión de pago de PAYMENTS (en minúsculas) -> (motivo del contrato, mensaje).
_PAGO_POR_MOTIVO: dict[str, tuple[str, str]] = {
    "fondos_insuficientes": ("FONDOS_INSUFICIENTES", MENSAJE_PAGO_FONDOS),
    "tarjeta_vencida": ("TARJETA_VENCIDA", MENSAJE_PAGO_TARJETA_VENCIDA),
    "pasarela_no_disponible": ("PASARELA_NO_DISPONIBLE", MENSAJE_PASARELA_NO_DISPONIBLE),
    "requiere_otra_tarjeta": ("REQUIERE_OTRA_TARJETA", MENSAJE_REQUIERE_OTRA_TARJETA),
    "sesion_vencida": ("SESION_VENCIDA", MENSAJE_SESION_VENCIDA),
    "sesion_no_encontrada": ("SESION_VENCIDA", MENSAJE_SESION_VENCIDA),
}


@dataclass
class ErrorFlujoDatos:
    """`motivo` es el del contrato (o `None` si el tipo de error no tiene)."""

    mensaje: str
    puede_reintentar: bool
    motivo: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _error_de(rechazo: FlujoRechazado) -> ErrorFlujoDatos:
    """Un rechazo de POLICY. Un motivo que no conocemos cae en el genérico con reintento: nunca
    deja pasar un código interno."""
    conocido = _POR_MOTIVO.get(rechazo.motivo)
    if conocido is None:
        logger.warning("POLICY rechazó con un motivo desconocido")
        return ErrorFlujoDatos(MENSAJE_FLUJO_NO_DISPONIBLE, True)
    motivo, mensaje, reintento = conocido
    extra = {}
    restantes = rechazo.detalle.get("intentos_restantes")
    if isinstance(restantes, int):
        extra["intentos_restantes"] = restantes
    return ErrorFlujoDatos(mensaje, reintento, motivo, extra)


def _error_servicio() -> ErrorFlujoDatos:
    return ErrorFlujoDatos(MENSAJE_FLUJO_NO_DISPONIBLE, True)


def _no_encontrada() -> ErrorFlujoDatos:
    return ErrorFlujoDatos(MENSAJE_PROPUESTA_NO_ENCONTRADA, False, "PROPUESTA_NO_ENCONTRADA")


@dataclass
class Autorizado:
    """Quién puede operar sobre la propuesta en la página del enlace."""

    propuesta_id: str
    acceso: Acceso


class ServicioFirmaPago:
    def __init__(
        self, clientes: Clientes, avisos_url: str = "", url_front: str = "http://localhost:5173"
    ) -> None:
        self._c = clientes
        self._avisos_url = avisos_url
        self._url_front = url_front

    # ---------- Autorización ----------

    async def _titular_de(self, usuario: UsuarioActual) -> str | None:
        """El titular verificado de un CLIENTE (HMAC de su cédula), o `None` si AUTH no lo tiene.
        Lanza `ErrorServicio` si AUTH no responde."""
        identidad = await self._c.auth.obtener_identidad(usuario.id)
        return identidad["titular_id"] if identidad else None

    async def autorizar_enlace(
        self, usuario: UsuarioActual | None, enlace_token: str | None, propuesta_id: str | None
    ) -> Autorizado | ErrorFlujoDatos:
        """W03-33: el token del enlace (con él se ignora `propuesta_id`) **o** la sesión de un
        CLIENTE, y entonces `propuesta_id` es obligatorio. Lo demás responde como una propuesta
        inexistente: no se revela cuál existe."""
        try:
            if enlace_token:
                resuelto = await self._c.policy.resolver_enlace(enlace_token)
                if resuelto is None:
                    return _no_encontrada()
                return Autorizado(resuelto["propuesta_id"], Acceso(enlace_token=enlace_token))
            if usuario is None or usuario.rol != "CLIENTE" or not propuesta_id:
                return _no_encontrada()
            titular = await self._titular_de(usuario)
        except FlujoRechazado as rechazo:
            return _error_de(rechazo)
        except ErrorServicio as error:
            logger.warning("no se pudo autorizar el acceso error=%s", error)
            return _error_servicio()
        if titular is None:
            return _no_encontrada()
        return Autorizado(propuesta_id, Acceso(titular_id=titular))

    async def _propuesta_del_asesor(
        self, usuario: UsuarioActual, propuesta_id: str
    ) -> dict | ErrorFlujoDatos:
        """El ASESOR opera solo sobre lo que él aceptó; una ajena responde como inexistente."""
        try:
            propuesta = await self._c.policy.obtener_propuesta(propuesta_id)
        except ErrorServicio as error:
            logger.warning("POLICY no disponible error=%s", error)
            return _error_servicio()
        if propuesta is None or propuesta.get("actor_usuario_id") != usuario.id:
            return _no_encontrada()
        return propuesta

    # ---------- ASESOR ----------

    async def enviar_codigo(
        self, usuario: UsuarioActual, propuesta_id: str, correo: str | None
    ) -> dict | ErrorFlujoDatos:
        previo = await self._propuesta_del_asesor(usuario, propuesta_id)
        if isinstance(previo, ErrorFlujoDatos):
            return previo
        return await self._llamar(self._c.policy.enviar_codigo(propuesta_id, correo))

    async def verificar_codigo(
        self, usuario: UsuarioActual, propuesta_id: str, codigo: str
    ) -> dict | ErrorFlujoDatos:
        previo = await self._propuesta_del_asesor(usuario, propuesta_id)
        if isinstance(previo, ErrorFlujoDatos):
            return previo
        return await self._llamar(self._c.policy.verificar_codigo(propuesta_id, codigo))

    async def emitir(self, usuario: UsuarioActual, propuesta_id: str) -> dict | ErrorFlujoDatos:
        """ASESOR: «Emitir». CLIENTE: «Firmar» (el titular sale de AUTH, nunca del cliente)."""
        if usuario.rol == "ASESOR":
            previo = await self._propuesta_del_asesor(usuario, propuesta_id)
            if isinstance(previo, ErrorFlujoDatos):
                return previo
            return await self._llamar(self._c.policy.emitir(propuesta_id, "ASESOR", None))
        try:
            titular = await self._titular_de(usuario)
        except ErrorServicio as error:
            logger.warning("AUTH no disponible al emitir error=%s", error)
            return _error_servicio()
        if titular is None:
            return _no_encontrada()
        return await self._llamar(self._c.policy.emitir(propuesta_id, "CLIENTE", titular))

    async def reenviar_enlace(
        self, usuario: UsuarioActual, propuesta_id: str
    ) -> dict | ErrorFlujoDatos:
        previo = await self._propuesta_del_asesor(usuario, propuesta_id)
        if isinstance(previo, ErrorFlujoDatos):
            return previo
        return await self._llamar(self._c.policy.reenviar_enlace(propuesta_id))

    # ---------- Página del enlace (token o sesión de CLIENTE) ----------

    async def estado_enlace(self, autorizado: Autorizado) -> dict | ErrorFlujoDatos:
        """El estado de la página: propuesta, vigencia, lo que se firma y, si ya hay, la firma y la
        póliza. El documento se pide solo mientras se puede firmar."""
        datos = await self._llamar(
            self._c.policy.estado_enlace(autorizado.propuesta_id, autorizado.acceso)
        )
        if isinstance(datos, ErrorFlujoDatos):
            return datos
        propuesta = datos["propuesta"]
        documento = None
        if datos["vigente"] and propuesta["estado"] == "EN_FIRMA":
            documento = await self._llamar(
                self._c.policy.contrato(autorizado.propuesta_id, autorizado.acceso)
            )
            documento = None if isinstance(documento, ErrorFlujoDatos) else documento
        poliza = None
        if propuesta["estado"] == "EMITIDA" and propuesta.get("poliza_id"):
            try:
                poliza = await self._c.policy.obtener_poliza(propuesta["poliza_id"])
            except ErrorServicio:
                logger.warning("POLICY no entregó la póliza de una propuesta emitida")
        return {**datos, "documento": documento, "poliza": poliza}

    async def subida_firma(
        self, autorizado: Autorizado, tipo_contenido: str, tamano_bytes: int
    ) -> dict | ErrorFlujoDatos:
        return await self._llamar(
            self._c.policy.subida_firma(
                autorizado.propuesta_id, autorizado.acceso, tipo_contenido, tamano_bytes
            )
        )

    async def firmar(self, autorizado: Autorizado, entrada: dict) -> dict | ErrorFlujoDatos:
        return await self._llamar(
            self._c.policy.firmar_enlace(autorizado.propuesta_id, autorizado.acceso, entrada)
        )

    async def contrato_pdf(self, autorizado: Autorizado) -> bytes | None | ErrorFlujoDatos:
        try:
            return await self._c.policy.contrato_pdf(autorizado.propuesta_id, autorizado.acceso)
        except FlujoRechazado as rechazo:
            return _error_de(rechazo)
        except ErrorServicio as error:
            logger.warning("POLICY no entregó el contrato error=%s", error)
            return _error_servicio()

    # ---------- Pago ----------

    async def iniciar_pago(self, autorizado: Autorizado, idioma: str) -> dict | ErrorFlujoDatos:
        # La pasarela devuelve al titular al front; el token del enlace no viaja en esta URL.
        url_retorno = f"{self._url_front}/pago/retorno/{autorizado.propuesta_id}"
        return await self._pago(
            self._c.policy.iniciar_pago(
                autorizado.propuesta_id, autorizado.acceso, url_retorno, idioma
            ),
            confirmando=False,
        )

    async def confirmar_pago(
        self, autorizado: Autorizado, sesion_id: str
    ) -> dict | ErrorFlujoDatos:
        return await self._pago(
            self._c.policy.confirmar_pago(autorizado.propuesta_id, autorizado.acceso, sesion_id),
            confirmando=True,
        )

    async def _pago(self, llamada, confirmando: bool) -> dict | ErrorFlujoDatos:
        """`{"sesion": {...}}` si hay una sesión abierta, `{"poliza": {...}}` con la póliza completa
        si ya se cobró, o un error de pago con su motivo del contrato."""
        resultado = await self._llamar(llamada)
        if isinstance(resultado, ErrorFlujoDatos):
            return resultado
        sesion, resumen = resultado.get("sesion"), resultado.get("poliza")
        if resumen is not None:
            try:
                completa = await self._c.policy.obtener_poliza(resumen["id"])
            except ErrorServicio:
                completa = None
            if completa is None:
                # Se cobró y se emitió, pero no pudimos leer la póliza: que vuelva a confirmar.
                return ErrorFlujoDatos(MENSAJE_FLUJO_NO_DISPONIBLE, True)
            return {"poliza": completa}
        if sesion is None:
            return _error_servicio()
        return self._sesion_a_resultado(sesion, confirmando)

    @staticmethod
    def _sesion_a_resultado(sesion: dict, confirmando: bool) -> dict | ErrorFlujoDatos:
        estado, motivo = sesion.get("estado"), (sesion.get("motivo") or "").lower()
        fallos = sesion.get("fallos_seguidos") or 0
        if estado == "abierta":
            if confirmando:
                # Regresó de la pasarela sin pagar: la sesión sigue abierta.
                return ErrorFlujoDatos(MENSAJE_PAGO_PENDIENTE, True, None, {"fallos": fallos})
            return {"sesion": sesion}
        if estado == "rechazada":
            contrato, mensaje = _PAGO_POR_MOTIVO.get(
                motivo, ("PAGO_RECHAZADO", MENSAJE_PAGO_RECHAZADO_TARJETA)
            )
        elif estado == "vencida":
            contrato, mensaje = "SESION_VENCIDA", MENSAJE_SESION_VENCIDA
        else:  # fallida, no_disponible o desconocido: la pasarela no respondió bien
            contrato, mensaje = _PAGO_POR_MOTIVO.get(
                motivo, ("PASARELA_NO_DISPONIBLE", MENSAJE_PASARELA_NO_DISPONIBLE)
            )
        # Siempre hay algo que intentar: reintentar, o con otra tarjeta si ya fallaron tres.
        return ErrorFlujoDatos(mensaje, True, contrato, {"fallos": fallos})

    # ---------- Listas, avisos y revocación ----------

    async def listar_propuestas(
        self, usuario: UsuarioActual, estado_visible: str | None, pagina: int, tamano: int
    ) -> dict:
        """Filtradas por el **rol del token**, nunca por un argumento: CLIENTE las de su titular,
        ASESOR las que aceptó y BACKOFFICE todas. Lanza `ErrorServicio` si algo no responde."""
        cliente_id = actor_id = None
        if usuario.rol == "CLIENTE":
            cliente_id = await self._titular_de(usuario)
            if cliente_id is None:  # sin identidad verificada no ve ninguna (W03-35)
                return {"propuestas": [], "total": 0, "pagina": pagina, "tamano": tamano}
        elif usuario.rol == "ASESOR":
            actor_id = usuario.id
        try:
            return await self._c.policy.listar_propuestas(
                cliente_id, actor_id, estado_visible, pagina, tamano
            )
        except FlujoRechazado as rechazo:
            raise ErrorServicio(rechazo.motivo) from rechazo

    async def nombres_de_asesores(self, propuestas: list[dict]) -> dict[str, str]:
        """Solo para el BACKOFFICE. De mejor esfuerzo: sin el nombre, la fila sale igual."""
        ids = sorted(
            {
                p["actor_usuario_id"]
                for p in propuestas
                if p.get("actor_rol") == "ASESOR" and p.get("actor_usuario_id")
            }
        )

        async def nombre(usuario_id: str) -> tuple[str, str | None]:
            try:
                datos = await self._c.auth.obtener_usuario(usuario_id)
            except ErrorServicio:
                return usuario_id, None
            return usuario_id, datos.get("nombre") if datos else None

        pares = await asyncio.gather(*(nombre(i) for i in ids))
        return {i: n for i, n in pares if n}

    async def ticket_avisos(self, usuario: UsuarioActual) -> dict:
        """Ticket de un solo uso de 60 s. Lanza `ErrorServicio` si no se pudo emitir."""
        tickets = self._c.tickets
        if tickets is None:
            raise ErrorServicio("sin Redis")
        titular = None
        try:
            if usuario.rol == "CLIENTE":
                titular = await self._titular_de(usuario)
            ticket, vence_en = await tickets.emitir(usuario.rol, usuario.id, titular)
        except Exception as error:
            raise ErrorServicio(type(error).__name__) from error
        return {"ticket": ticket, "url": self._avisos_url, "vence_en": vence_en}

    async def consentimiento_revocado(self, titular_id: str | None, consentimiento_id: str) -> None:
        """W01-A2: avisa a POLICY para que purgue el contacto y la imagen de la firma. De mejor
        esfuerzo: si POLICY no responde, la revocación no falla (la purga diaria la retoma)."""
        if not titular_id:
            return
        try:
            await self._c.policy.consentimiento_revocado(titular_id, consentimiento_id)
        except ErrorServicio as error:
            logger.warning(
                "POLICY no recibió la revocación consentimiento=%s error=%s",
                id_opaco(consentimiento_id),
                error,
            )

    # ---------- Interno ----------

    @staticmethod
    async def _llamar(llamada) -> Any:
        try:
            return await llamada
        except FlujoRechazado as rechazo:
            return _error_de(rechazo)
        except ErrorServicio as error:
            logger.warning("POLICY no disponible error=%s", error)
            return _error_servicio()


@dataclass
class DocumentoDelCliente:
    """El documento con el que cotiza un CLIENTE, o el error de por qué no."""

    documento: str | None = None
    error: str | None = None


async def documento_del_cliente(
    clientes: Clientes,
    usuario: UsuarioActual | None,
    documento_enviado: str | None,
    clave_titular: str,
) -> DocumentoDelCliente:
    """W03-34: un CLIENTE que no manda `documentoIdentidad` cotiza con el de su cuenta (AUTH lo
    entrega en memoria y no se guarda ni se loguea). Si lo manda, debe ser el suyo: un CLIENTE no
    cotiza con la cédula de otro. El ASESOR lo escribe siempre. Si AUTH no responde o el CLIENTE no
    tiene identidad verificada, se queda lo que mandó (y sin nada, el llamador lo pide)."""
    if usuario is None or usuario.rol != "CLIENTE":
        return DocumentoDelCliente(documento_enviado)
    try:
        identidad = await clientes.auth.obtener_identidad(usuario.id)
    except ErrorServicio:
        logger.warning("AUTH no disponible al tomar el documento del cliente")
        return DocumentoDelCliente(documento_enviado)
    if identidad is None:
        return DocumentoDelCliente(documento_enviado)
    if not documento_enviado:
        return DocumentoDelCliente(identidad["documento"])
    if cliente_id_desde_documento(documento_enviado, clave_titular) != identidad["titular_id"]:
        return DocumentoDelCliente(error=MENSAJE_DOCUMENTO_NO_COINCIDE)
    return DocumentoDelCliente(documento_enviado)
