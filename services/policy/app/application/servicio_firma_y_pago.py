"""Firma y pago con el asesor o en la sesión del CLIENTE (HU-W03, bloque 9: W03-19 a W03-34).

    ASESOR o SOCIO (HU-W07, por la API): ACEPTADA ─código─► IDENTIDAD_VERIFICADA ─«Emitir» (UNDER)─►
             EN_FIRMA + enlace de 30 min
    CLIENTE: ACEPTADA ─────────────────────────────── «Firmar» (UNDER)─► EN_FIRMA (en su sesión)
    los dos: EN_FIRMA ─firma (trazo, imagen o nombre)─► FIRMADA ─pago en la pasarela─► EMITIDA

UNDER y KYC deciden antes de mostrar el contrato firmable: no firma quien luego se rechaza (W03-21).
La póliza se emite **en la misma respuesta** que confirma el pago (W03-29); el correo con el PDF y
el aviso al asesor van por Cloud Tasks.

**Autorización (W03-33):** las operaciones de la página del enlace aceptan el token del enlace
(POLICY guarda solo su huella y lo valida) **o** el titular de la sesión de un CLIENTE, que el BFF
toma de AUTH. Una propuesta ajena, un token que no existe o ninguno de los dos responden igual que
una propuesta inexistente. Un enlace vencido o anulado responde `ENLACE_VENCIDO`, salvo al confirmar
un pago cuya sesión se abrió antes (W03-22: no se pierde un cobro hecho).

Nada de esto escribe en los logs el contacto, el código, el token ni la imagen (regla 6)."""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.application.avisos import AvisosNulos, PuertoAvisos
from app.application.cliente_acl import (
    PuertoClienteAcl,
    PuertoNotificador,
    SolicitudFirma,
    SolicitudNotificacion,
)
from app.application.cliente_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoRechazado,
    PuertoAlmacenamiento,
)
from app.application.cliente_payments import (
    PaymentsNoDisponible,
    PuertoSesionesPago,
    SesionPago,
    SesionRechazada,
)
from app.application.cliente_under import PuertoUnder, UnderNoDisponible
from app.application.documento_firma import (
    VERSION_DOCUMENTO,
    DocumentoNoGenerable,
    construir_documento,
    hash_documento,
    serializar,
)
from app.application.huellas import (
    coincide,
    enmascarar_correo,
    generar_codigo,
    generar_token,
    huella,
)
from app.application.identificadores import id_opaco
from app.application.imagen_firma import ImagenInvalida, validar

logger = logging.getLogger("policy.firma_y_pago")

MODOS_FIRMA = ("TRAZO", "IMAGEN", "NOMBRE")
# Quien vende por el titular: verifica su identidad con el código y «emite» (W07-20). Los avisos en
# vivo al asesor siguen siendo solo del ASESOR (`avisos.py`); no hay correo al asesor.
ROLES_INTERMEDIARIO = ("ASESOR", "SOCIO")
CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Motivos (los del contrato v0.18, en mayúsculas) y su estado HTTP.
NO_ENCONTRADA = "PROPUESTA_NO_ENCONTRADA"
NO_VALIDA = "PROPUESTA_NO_VALIDA"
CODIGO_INVALIDO = "CODIGO_INVALIDO"
CODIGO_VENCIDO = "CODIGO_VENCIDO"
INTENTOS_AGOTADOS = "INTENTOS_AGOTADOS"
ENLACE_VENCIDO = "ENLACE_VENCIDO"
IMAGEN_INVALIDA = "IMAGEN_INVALIDA"
COBRO_NO_AUTORIZADO = "COBRO_NO_AUTORIZADO"
VERSION_DESACTUALIZADA = "VERSION_DOCUMENTO_DESACTUALIZADA"
FIRMA_NO_DISPONIBLE = "SERVICIO_FIRMA_NO_DISPONIBLE"
NO_DISPONIBLE = "SERVICIO_NO_DISPONIBLE"
DATOS_INVALIDOS = "DATOS_INVALIDOS"

ESTADO_HTTP = {
    NO_ENCONTRADA: 404,
    NO_VALIDA: 409,
    ENLACE_VENCIDO: 409,
    CODIGO_INVALIDO: 422,
    CODIGO_VENCIDO: 422,
    INTENTOS_AGOTADOS: 422,
    IMAGEN_INVALIDA: 422,
    COBRO_NO_AUTORIZADO: 422,
    VERSION_DESACTUALIZADA: 409,
    DATOS_INVALIDOS: 422,
    FIRMA_NO_DISPONIBLE: 503,
    NO_DISPONIBLE: 503,
}


class ErrorFlujo(Exception):
    """Un rechazo con su motivo del contrato. `detalle`: datos para el mensaje (nunca
    personales)."""

    def __init__(self, motivo: str, **detalle: Any) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.detalle = detalle

    @property
    def estado_http(self) -> int:
        return ESTADO_HTTP.get(self.motivo, 409)


@dataclass(frozen=True)
class Autorizacion:
    """Lo que el BFF trae: el token del enlace (`X-Enlace-Firma`) o el titular de la sesión."""

    enlace_token: str | None = None
    titular_id: str | None = None


@dataclass(frozen=True)
class Reglas:
    clave_huellas: bytes
    url_front: str
    codigo_vigencia: timedelta = timedelta(minutes=10)
    codigo_max_intentos: int = 5
    enlace_vigencia: timedelta = timedelta(minutes=30)


class PuertoRepositorio(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def contacto(self, propuesta_id: str) -> dict[str, str | None] | None: ...

    def nombres_titulares(self, propuesta_ids: list[str]) -> dict[str, str]: ...

    def actualizar_correo(self, propuesta_id: str, correo: str) -> None: ...

    def cambiar_estado(
        self, propuesta_id: str, nuevo: str, desde: tuple[str, ...], motivo: str | None = None
    ) -> bool: ...

    def crear_verificacion(self, propuesta_id: str, huella: str, vence_en: datetime) -> None: ...

    def verificacion_vigente(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def sumar_intento_codigo(self, verificacion_id: str) -> int: ...

    def marcar_verificada(self, verificacion_id: str, propuesta_id: str) -> bool: ...

    def crear_enlace(self, propuesta_id: str, huella: str, vence_en: datetime) -> None: ...

    def enlace_por_huella(self, huella: str) -> dict[str, Any] | None: ...

    def enlace_activo(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def registrar_firma_enlace(
        self, propuesta_id: str, evidencia: dict[str, Any], firma_texto: str | None
    ) -> tuple[dict, bool]: ...


class ServicioFirmaYPago:
    def __init__(
        self,
        repositorio: PuertoRepositorio,
        under: PuertoUnder,
        acl: PuertoClienteAcl,
        notificador: PuertoNotificador,
        almacenamiento: PuertoAlmacenamiento,
        pagos: PuertoSesionesPago,
        emitir_pagada: Callable[[str], dict[str, Any] | None],
        reglas: Reglas,
        avisos: PuertoAvisos | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._under = under
        self._acl = acl
        self._notificador = notificador
        self._almacen = almacenamiento
        self._pagos = pagos
        self._emitir_pagada = emitir_pagada
        self._reglas = reglas
        self._avisos = avisos or AvisosNulos()
        self._reloj = reloj

    # ------------------------------------------------------------------ autorización

    def _propuesta(self, propuesta_id: str) -> dict[str, Any]:
        propuesta = self._repo.obtener(propuesta_id)
        if propuesta is None:
            raise ErrorFlujo(NO_ENCONTRADA)
        return propuesta

    def nombres_titulares(self, propuesta_ids: list[str]) -> dict[str, str]:
        return self._repo.nombres_titulares(propuesta_ids)

    def autorizar(
        self, propuesta_id: str, auth: Autorizacion, permitir_vencido: bool = False
    ) -> dict[str, Any]:
        """La propuesta si el token o el titular la autorizan (W03-33); si no, `NO_ENCONTRADA`."""
        propuesta = self._propuesta(propuesta_id)
        if auth.titular_id:
            if auth.titular_id != propuesta["cliente_id"]:
                raise ErrorFlujo(NO_ENCONTRADA)
            return propuesta
        if not auth.enlace_token:
            raise ErrorFlujo(NO_ENCONTRADA)
        enlace = self._repo.enlace_por_huella(huella(auth.enlace_token, self._reglas.clave_huellas))
        if enlace is None or enlace["propuesta_id"] != propuesta_id:
            raise ErrorFlujo(NO_ENCONTRADA)
        vencido = enlace["anulado_en"] is not None or enlace["vence_en"] <= self._reloj()
        if vencido and not permitir_vencido:
            raise ErrorFlujo(ENLACE_VENCIDO)
        return propuesta

    def resolver_enlace(self, token: str) -> dict[str, Any] | None:
        """De qué propuesta es un token y si sigue vigente (la página del enlace no lleva el id).
        `None` si no existe. Un enlace vencido o anulado se devuelve con `vigente=False`."""
        enlace = self._repo.enlace_por_huella(huella(token, self._reglas.clave_huellas))
        if enlace is None:
            return None
        vigente = enlace["anulado_en"] is None and enlace["vence_en"] > self._reloj()
        return {
            "propuesta_id": enlace["propuesta_id"],
            "vigente": vigente,
            "vence_en": enlace["vence_en"],
        }

    # ------------------------------------------------------------------ código (W03-20)

    def _correo_del_titular(self, propuesta: dict[str, Any]) -> str:
        if propuesta.get("origen_contacto") != "PROPUESTA" or propuesta.get("contacto_borrado_en"):
            raise ErrorFlujo(NO_VALIDA)
        correo = (self._repo.contacto(propuesta["id"]) or {}).get("correo")
        if not correo:
            raise ErrorFlujo(NO_VALIDA)
        return correo

    def enviar_codigo(self, propuesta_id: str, correo: str | None = None) -> dict[str, Any]:
        """Solo ASESOR (lo exige el BFF). Envía un código de 6 dígitos al correo del titular; pedir
        otro anula el anterior. `correo` corrige el que se capturó (W03-25)."""
        propuesta = self._propuesta(propuesta_id)
        if propuesta["estado"] != "ACEPTADA":
            raise ErrorFlujo(NO_VALIDA)
        if correo is not None:
            correo = correo.strip().lower()
            if not CORREO_VALIDO.match(correo) or len(correo) > 254:
                raise ErrorFlujo(DATOS_INVALIDOS, campo="correo")
            self._correo_del_titular(propuesta)  # solo se corrige un contacto de la propuesta
            self._repo.actualizar_correo(propuesta_id, correo)
        destino = self._correo_del_titular(propuesta)
        codigo = generar_codigo()
        vence_en = self._reloj() + self._reglas.codigo_vigencia
        self._repo.crear_verificacion(
            propuesta_id, huella(f"{propuesta_id}:{codigo}", self._reglas.clave_huellas), vence_en
        )
        self._notificar(
            f"codigo-verificacion-{uuid.uuid4()}",
            "codigo-verificacion",
            propuesta,
            destino,
            {
                "codigo": codigo,
                "minutos": str(int(self._reglas.codigo_vigencia.total_seconds() // 60)),
            },
        )
        logger.info("código enviado propuesta=%s", id_opaco(propuesta_id))
        return {
            "propuesta_id": propuesta_id,
            "correo_enmascarado": enmascarar_correo(destino),
            "vence_en": vence_en,
        }

    def verificar_codigo(self, propuesta_id: str, codigo: str) -> dict[str, Any]:
        propuesta = self._propuesta(propuesta_id)
        if propuesta["estado"] == "IDENTIDAD_VERIFICADA":
            return propuesta
        if propuesta["estado"] != "ACEPTADA":
            raise ErrorFlujo(NO_VALIDA)
        verificacion = self._repo.verificacion_vigente(propuesta_id)
        if verificacion is None:
            raise ErrorFlujo(NO_VALIDA)
        maximo = self._reglas.codigo_max_intentos
        if verificacion["intentos"] >= maximo:
            raise ErrorFlujo(INTENTOS_AGOTADOS, intentos_restantes=0)
        if verificacion["vence_en"] <= self._reloj():
            raise ErrorFlujo(CODIGO_VENCIDO)
        valido = coincide(
            f"{propuesta_id}:{codigo.strip()}", verificacion["huella"], self._reglas.clave_huellas
        )
        if not valido:
            intentos = self._repo.sumar_intento_codigo(verificacion["id"])
            restantes = max(0, maximo - intentos)
            if restantes == 0:
                raise ErrorFlujo(INTENTOS_AGOTADOS, intentos_restantes=0)
            raise ErrorFlujo(CODIGO_INVALIDO, intentos_restantes=restantes)
        self._repo.marcar_verificada(verificacion["id"], propuesta_id)
        logger.info("identidad verificada propuesta=%s", id_opaco(propuesta_id))
        actualizada = self._propuesta(propuesta_id)
        self._avisos.publicar(actualizada, actualizada["estado"])
        return actualizada

    # ------------------------------------------------------------------ «Emitir» / «Firmar»
    # (W03-21)

    def emitir(self, propuesta_id: str, rol: str, titular_id: str | None = None) -> dict[str, Any]:
        """ASESOR («Emitir», con la identidad verificada) o CLIENTE titular («Firmar»). UNDER y KYC
        deciden aquí. Devuelve la propuesta y, para el ASESOR, el enlace enviado (sin la URL)."""
        propuesta = self._propuesta(propuesta_id)
        if rol == "CLIENTE":
            if not titular_id or titular_id != propuesta["cliente_id"]:
                raise ErrorFlujo(NO_ENCONTRADA)
            desde = ("ACEPTADA",)
        elif rol in ROLES_INTERMEDIARIO:
            desde = ("IDENTIDAD_VERIFICADA",)
        else:
            raise ErrorFlujo(NO_VALIDA)
        if propuesta["estado"] in ("EN_FIRMA", "FIRMADA", "EMITIDA", "NO_EMITIDA", "EN_REVISION"):
            return {"propuesta": propuesta, "enlace": None}  # ya se decidió: no se repite
        if propuesta["estado"] not in desde:
            raise ErrorFlujo(NO_VALIDA)
        try:
            decision = self._under.decidir(
                propuesta_id,
                propuesta["cliente_id"],
                propuesta.get("nivel_riesgo"),
                propuesta["cobertura"],
            )
        except UnderNoDisponible as error:
            raise ErrorFlujo(NO_DISPONIBLE) from error
        if decision.decision == "APROBADA":
            nuevo, motivo = "EN_FIRMA", None
        elif decision.decision == "RECHAZADA":
            nuevo, motivo = "NO_EMITIDA", ",".join(decision.motivos) or None
        else:
            nuevo, motivo = "EN_REVISION", ",".join(decision.motivos) or None
        self._repo.cambiar_estado(propuesta_id, nuevo, desde, motivo)
        actualizada = self._propuesta(propuesta_id)
        logger.info("decisión propuesta=%s -> %s", id_opaco(propuesta_id), actualizada["estado"])
        self._avisos.publicar(actualizada, actualizada["estado"])
        enlace = None
        if actualizada["estado"] == "EN_FIRMA" and rol in ROLES_INTERMEDIARIO:
            enlace = self._enviar_enlace(actualizada)
        return {"propuesta": actualizada, "enlace": enlace}

    def vencimiento_enlace(self, propuesta_id: str) -> datetime | None:
        """Cuándo vence el enlace activo (HU-W07: el socio sigue la propuesta sin ver el enlace)."""
        enlace = self._repo.enlace_activo(propuesta_id)
        return enlace["vence_en"] if enlace else None

    def reenviar_enlace(self, propuesta_id: str) -> dict[str, Any]:
        """Solo ASESOR: un enlace nuevo que anula el anterior; el titular retoma donde iba."""
        propuesta = self._propuesta(propuesta_id)
        if propuesta["estado"] not in ("EN_FIRMA", "FIRMADA"):
            raise ErrorFlujo(NO_VALIDA)
        return {"propuesta": propuesta, "enlace": self._enviar_enlace(propuesta)}

    def _enviar_enlace(self, propuesta: dict[str, Any]) -> dict[str, Any]:
        destino = self._correo_del_titular(propuesta)
        token = generar_token()
        vence_en = self._reloj() + self._reglas.enlace_vigencia
        huella_token = huella(token, self._reglas.clave_huellas)
        self._repo.crear_enlace(propuesta["id"], huella_token, vence_en)
        # El token va en el fragmento (`#t=`): el navegador no lo envía al servidor (W03-23).
        self._notificar(
            f"enlace-firma-{huella_token[:24]}",
            "enlace-firma",
            propuesta,
            destino,
            {
                "enlace": f"{self._reglas.url_front}/firma#t={token}",
                "minutos": str(int(self._reglas.enlace_vigencia.total_seconds() // 60)),
            },
        )
        logger.info("enlace de firma enviado propuesta=%s", id_opaco(propuesta["id"]))
        return {"correo_enmascarado": enmascarar_correo(destino), "vence_en": vence_en}

    def _notificar(
        self, id_notificacion: str, plantilla: str, propuesta: dict, correo: str, datos: dict
    ) -> None:
        resultado = self._notificador.notificar(
            SolicitudNotificacion(
                id_notificacion=id_notificacion,
                plantilla=plantilla,
                destinatario_id=propuesta["cliente_id"],
                datos=datos,
                correo=correo,
            )
        )
        if resultado.estado != "enviada":
            raise ErrorFlujo(NO_DISPONIBLE)

    # ------------------------------------------------------------------ página del enlace

    def documento(self, propuesta_id: str, auth: Autorizacion) -> dict[str, Any]:
        propuesta = self.autorizar(propuesta_id, auth)
        try:
            return construir_documento(propuesta)
        except DocumentoNoGenerable as error:
            raise ErrorFlujo(NO_VALIDA) from error

    def estado_para_enlace(self, propuesta_id: str, auth: Autorizacion) -> dict[str, Any]:
        """Lo que la página del enlace necesita: la propuesta, si el enlace sigue vigente y la
        evidencia si ya firmó. Un enlace vencido no falla aquí: la página muestra el estado
        vacío."""
        try:
            propuesta = self.autorizar(propuesta_id, auth)
            vigente = True
        except ErrorFlujo as error:
            if error.motivo != ENLACE_VENCIDO:
                raise
            propuesta = self.autorizar(propuesta_id, auth, permitir_vencido=True)
            vigente = False
        enlace = self._repo.enlace_activo(propuesta_id) if auth.enlace_token else None
        return {
            "propuesta": propuesta,
            "vigente": vigente,
            "vence_en": enlace["vence_en"] if enlace else None,
            "evidencia": self._repo.obtener_evidencia(propuesta_id),
        }

    def solicitar_subida(
        self, propuesta_id: str, auth: Autorizacion, tipo_contenido: str, tamano: int
    ) -> Any:
        propuesta = self.autorizar(propuesta_id, auth)
        if propuesta["estado"] != "EN_FIRMA":
            raise ErrorFlujo(NO_VALIDA)
        try:
            return self._almacen.solicitar_subida(
                propuesta_id, propuesta.get("consentimiento_id"), tipo_contenido, tamano
            )
        except ArchivoRechazado as error:
            raise ErrorFlujo(IMAGEN_INVALIDA, codigo=error.codigo) from error
        except AlmacenamientoNoDisponible as error:
            raise ErrorFlujo(NO_DISPONIBLE) from error

    def firmar(
        self,
        propuesta_id: str,
        auth: Autorizacion,
        version_documento: str,
        acepta_terminos: bool,
        autoriza_cobro: bool,
        modo: str,
        imagen_archivo_id: str | None = None,
        nombre_firma: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Firma siempre el titular (W03-8). Firmar dos veces devuelve la misma evidencia."""
        propuesta = self.autorizar(propuesta_id, auth)
        existente = self._repo.obtener_evidencia(propuesta_id)
        if existente is not None:
            return existente, propuesta["estado"]
        if propuesta["estado"] != "EN_FIRMA":
            raise ErrorFlujo(NO_VALIDA)
        if not (acepta_terminos and autoriza_cobro):
            raise ErrorFlujo(COBRO_NO_AUTORIZADO)
        if version_documento != VERSION_DOCUMENTO:
            raise ErrorFlujo(VERSION_DESACTUALIZADA)
        if modo not in MODOS_FIRMA:
            raise ErrorFlujo(DATOS_INVALIDOS, campo="modo")
        texto = None
        if modo == "NOMBRE":
            texto = " ".join((nombre_firma or "").split())
            if not 2 <= len(texto) <= 100:
                raise ErrorFlujo(DATOS_INVALIDOS, campo="nombreFirma")
        else:
            if not imagen_archivo_id:
                raise ErrorFlujo(DATOS_INVALIDOS, campo="imagenArchivoId")
            self._validar_imagen(propuesta_id, imagen_archivo_id)
        try:
            documento = serializar(construir_documento(propuesta))
        except DocumentoNoGenerable as error:
            raise ErrorFlujo(NO_VALIDA) from error
        documento_hash = hash_documento(documento)
        resultado = self._acl.firmar(
            SolicitudFirma(
                id_firma=propuesta_id,
                documento_hash=documento_hash,
                version_documento=VERSION_DOCUMENTO,
                firmante_id=propuesta["cliente_id"],
            )
        )
        if resultado.estado != "firmada" or not resultado.referencia or not resultado.firmada_en:
            raise ErrorFlujo(FIRMA_NO_DISPONIBLE)
        evidencia, creada = self._repo.registrar_firma_enlace(
            propuesta_id,
            {
                "id": str(uuid.uuid4()),
                "documento": documento,
                "documento_hash": documento_hash,
                "version_documento": VERSION_DOCUMENTO,
                # El titular (HMAC del documento), tenga o no cuenta: es quien firma.
                "firmante_id": propuesta["cliente_id"],
                "consentimiento_id": propuesta.get("consentimiento_id"),
                "referencia_proveedor": resultado.referencia,
                "firmada_en": datetime.fromisoformat(resultado.firmada_en),
                "modo_firma": modo,
                "imagen_firma_archivo_id": imagen_archivo_id if modo != "NOMBRE" else None,
            },
            texto,
        )
        actualizada = self._propuesta(propuesta_id)
        if creada:
            logger.info("firma registrada propuesta=%s modo=%s", id_opaco(propuesta_id), modo)
            self._avisos.publicar(actualizada, actualizada["estado"])
        return evidencia, actualizada["estado"]

    def _validar_imagen(self, propuesta_id: str, archivo_id: str) -> None:
        """Confirma el archivo en ALMACENAMIENTO (tipo real y tamaño), lo baja y valida dimensiones
        y fondo (plan principal 1.5). Una imagen que POLICY rechaza se marca para borrar."""
        try:
            archivo = self._almacen.confirmar(archivo_id)
            if (
                archivo.get("propietario_id") != propuesta_id
                or archivo.get("tipo") != "firma-imagen"
            ):
                raise ErrorFlujo(NO_ENCONTRADA)
            if archivo.get("estado") != "DISPONIBLE":
                raise ErrorFlujo(IMAGEN_INVALIDA, codigo=archivo.get("motivo") or "RECHAZADO")
            contenido = self._almacen.bajar(archivo_id)
        except ArchivoRechazado as error:
            raise ErrorFlujo(IMAGEN_INVALIDA, codigo=error.codigo) from error
        except AlmacenamientoNoDisponible as error:
            raise ErrorFlujo(NO_DISPONIBLE) from error
        try:
            validar(contenido)
        except ImagenInvalida as error:
            try:
                self._almacen.retener(archivo_id)
            except (ArchivoRechazado, AlmacenamientoNoDisponible):
                logger.warning("no se pudo marcar para borrar una imagen rechazada")
            raise ErrorFlujo(IMAGEN_INVALIDA, codigo=error.motivo) from error

    # ------------------------------------------------------------------ pago (W03-28 a W03-30)

    def iniciar_pago(
        self, propuesta_id: str, auth: Autorizacion, url_retorno: str, idioma: str = "es"
    ) -> dict[str, Any]:
        """Abre (o reabre) la sesión de pago de una propuesta FIRMADA. Si PAYMENTS dice que ya se
        cobró, emite la póliza en vez de abrir otra."""
        propuesta = self.autorizar(propuesta_id, auth)
        if propuesta["estado"] == "EMITIDA":
            return {"sesion": None, "poliza": self._emitir_pagada(propuesta_id)}
        if propuesta["estado"] != "FIRMADA":
            raise ErrorFlujo(NO_VALIDA)
        prima = propuesta["prima"]
        try:
            sesion = self._pagos.abrir_sesion(
                propuesta_id,
                propuesta["cliente_id"],
                prima["valor"],
                prima["moneda"],
                "Seguro de vida para tu crédito hipotecario",
                url_retorno,
                idioma,
            )
        except PaymentsNoDisponible as error:
            raise ErrorFlujo(NO_DISPONIBLE) from error
        except SesionRechazada as error:
            logger.warning("la pasarela no aceptó la sesión propuesta=%s", id_opaco(propuesta_id))
            raise ErrorFlujo(DATOS_INVALIDOS, campo="urlRetorno") from error
        return self._resultado_pago(propuesta, sesion)

    def confirmar_pago(
        self, propuesta_id: str, auth: Autorizacion, sesion_id: str
    ) -> dict[str, Any]:
        """Al regresar de la pasarela. Confirma de servidor a servidor y, si se cobró, emite la
        póliza en la misma respuesta (W03-29). Repetirlo no cobra ni emite dos veces."""
        # Una sesión abierta con el enlace vigente se acepta aunque el enlace ya haya vencido.
        propuesta = self.autorizar(propuesta_id, auth, permitir_vencido=True)
        if propuesta["estado"] == "EMITIDA":
            return {"sesion": None, "poliza": self._emitir_pagada(propuesta_id)}
        if propuesta["estado"] != "FIRMADA":
            raise ErrorFlujo(NO_VALIDA)
        try:
            sesion = self._pagos.confirmar_sesion(sesion_id, propuesta_id)
        except PaymentsNoDisponible as error:
            raise ErrorFlujo(NO_DISPONIBLE) from error
        if sesion is None:
            raise ErrorFlujo(NO_ENCONTRADA)
        return self._resultado_pago(propuesta, sesion)

    def _resultado_pago(self, propuesta: dict[str, Any], sesion: SesionPago) -> dict[str, Any]:
        if sesion.estado == "cobrada":
            poliza = self._emitir_pagada(propuesta["id"])
            return {"sesion": sesion, "poliza": poliza}
        if sesion.estado == "rechazada":
            # El titular elige otra tarjeta sin perder la firma (W03-30): la propuesta sigue
            # FIRMADA.
            self._repo.cambiar_estado(propuesta["id"], "FIRMADA", ("FIRMADA",), "PAGO_RECHAZADO")
        return {"sesion": sesion, "poliza": None}
