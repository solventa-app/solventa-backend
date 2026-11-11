"""Orquestación de `solicitarOferta` (T-W01-7, CA-W01-01..06).

El BFF no calcula nada de negocio: secuencia AUTH → ACL Worker (5 fuentes en
paralelo) → RISK → RATING y traduce cualquier falla a `ErrorOferta` u oferta
preliminar (regla #4: nunca un 5xx crudo). Ver "Quién orquesta" en
`docs/arquitectura-backend.md`."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.clientes import (
    Clientes,
    DatosInsuficientes,
    ErrorServicio,
    SolicitudInvalida,
    VehiculoRechazado,
)
from app.identificadores import (
    cliente_id_desde_documento,
    enmascarar_documento,
    enmascarar_placa,
    id_opaco,
    normalizar_placa,
    vehiculo_id_desde_placa,
)
from app.jurisdicciones import JURISDICCIONES, PLAZO_MAXIMO_MESES, PLAZO_MINIMO_MESES

logger = logging.getLogger("bff.oferta")

_FUENTES_POR_DEFECTO = (
    "cuentas-bancarias",
    "historial-crediticio",
    "afiliacion-pila",
    "camara-comercio",
    "antecedentes-judiciales",
)


def cargar_fuentes(ruta: Path | str | None = None) -> tuple[str, ...]:
    """Lee la configuración de fuentes (Bloque 5). Orden: la ruta dada, `FUENTES_CONFIG_PATH`, la
    copia de la imagen en `app/fuentes.json` (idéntica al contrato; lo verifica
    `scripts/sincronizar_fuentes.py --verificar`) y, en el repo, `contracts/fuentes.json`. Sin
    archivo, las 5 fuentes por defecto."""
    candidatas = [Path(ruta)] if ruta is not None else []
    if ruta is None and os.getenv("FUENTES_CONFIG_PATH"):
        candidatas.append(Path(os.environ["FUENTES_CONFIG_PATH"]))
    if ruta is None:
        aqui = Path(__file__).resolve()
        candidatas.append(aqui.parent / "fuentes.json")
        candidatas.extend(p / "contracts" / "fuentes.json" for p in list(aqui.parents)[:5])
    candidata = next((c for c in candidatas if c.is_file()), None)
    if candidata is not None:
        datos = json.loads(candidata.read_text(encoding="utf-8"))
        return tuple(item["nombre"] for item in datos)
    return _FUENTES_POR_DEFECTO


FUENTES = cargar_fuentes()

MENSAJE_SIN_CONSENTIMIENTO = "No hay un consentimiento vigente para consultar tus datos."
MENSAJE_REINTENTAR = (
    "No pudimos calcular tu oferta en este momento. Intenta de nuevo o habla con un asesor."
)
# T18: los límites del plazo salen de jurisdicciones.json, no de números escritos aquí.
MENSAJE_PLAZO_INVALIDO = (
    f"El plazo del crédito debe estar entre {PLAZO_MINIMO_MESES} y {PLAZO_MAXIMO_MESES} meses."
)
MENSAJE_VALOR_INVALIDO = "El valor del crédito no es válido."
MENSAJE_DOCUMENTO_INVALIDO = "El documento de identidad no es válido."
MENSAJE_DIRECCION_INVALIDA = "Ingresa la dirección del inmueble."
# T19: Google confirmó el lugar, pero está en un país que Solventa no atiende.
MENSAJE_DIRECCION_PAIS_NO_SOPORTADO = "La dirección del inmueble está en un país que no atendemos."
MENSAJE_DATOS_INSUFICIENTES = (
    "No pudimos consultar tu historial crediticio en este momento. "
    "Intenta de nuevo o habla con un asesor."
)

MOTIVO_CONSENTIMIENTO_NO_VIGENTE = "CONSENTIMIENTO_NO_VIGENTE"
MOTIVO_DATOS_INSUFICIENTES = "DATOS_INSUFICIENTES"
MOTIVO_FUENTES_NO_DISPONIBLES = "FUENTES_NO_DISPONIBLES"

# ---------- KAN-290: póliza de auto ----------
FUENTES_VEHICULO = ("runt-vehiculo", "fasecolda-valor", "siniestralidad-placa")
MENSAJE_PLACA_INVALIDA = "La placa no es válida. Escríbela como ABC123 (o ABC12D si es una moto)."
MENSAJE_KILOMETRAJE_INVALIDO = "El kilometraje anual debe estar entre 0 y 200.000 km."
MENSAJE_CIUDAD_INVALIDA = "Ingresa la ciudad donde circula el vehículo."
MENSAJE_DATOS_VEHICULO = (
    "No pudimos consultar los datos del vehículo en este momento. Intenta de nuevo en unos minutos."
)
KILOMETRAJE_MAXIMO = 200_000

# Mismos límites que el formulario W01-01 (solventa-frontend: SolicitudYConsentimiento.vue).
MIN_DIGITOS_DOCUMENTO = 6
MAX_LARGO_DOCUMENTO = 20
MAX_LARGO_DIRECCION = 200


@dataclass
class ErrorOfertaDatos:
    mensaje: str
    puede_reintentar: bool
    motivo: str | None = None


def validar_solicitud(
    documento_identidad: str,
    valor_credito: str,
    plazo_meses: int,
    direccion_inmueble: str,
    documento_tipado: bool = False,
) -> str | None:
    """Mensaje para el usuario si la solicitud no es válida, o `None`. Se valida antes de
    tocar el consentimiento o las fuentes: un dato mal escrito no gasta una consulta. T20: un
    documento con tipo y país ya se validó con el formato de su país (`documento_tipado`)."""
    documento = documento_identidad.strip()
    digitos = sum(c.isdigit() for c in documento)
    if not documento_tipado and (
        digitos < MIN_DIGITOS_DOCUMENTO or len(documento) > MAX_LARGO_DOCUMENTO
    ):
        return MENSAJE_DOCUMENTO_INVALIDO
    try:
        valor = Decimal(valor_credito)
    except InvalidOperation:
        return MENSAJE_VALOR_INVALIDO
    if not valor.is_finite() or valor <= 0:
        return MENSAJE_VALOR_INVALIDO
    if not PLAZO_MINIMO_MESES <= plazo_meses <= PLAZO_MAXIMO_MESES:
        return MENSAJE_PLAZO_INVALIDO
    direccion = direccion_inmueble.strip()
    if not direccion or len(direccion) > MAX_LARGO_DIRECCION:
        return MENSAJE_DIRECCION_INVALIDA
    return None


class ServicioOferta:
    def __init__(self, clientes: Clientes, cliente_id_clave: str) -> None:
        self._clientes = clientes
        self._clave = cliente_id_clave

    async def solicitar(
        self,
        consentimiento_id: str,
        documento_identidad: str,
        valor_credito: str,
        plazo_meses: int,
        direccion_inmueble: str,
        tipo_documento: str | None = None,
        pais: str | None = None,
        place_id: str | None = None,
    ) -> dict | ErrorOfertaDatos:
        """Devuelve la oferta tal como la entrega RATING (ya en el camelCase del
        contrato) o un `ErrorOfertaDatos`. El plazo y la dirección los usa RATING en la
        prima; ni el documento ni la dirección se loguean nunca (regla #6)."""
        # 0. Datos de la solicitud (B3): un error aquí no se arregla reintentando.
        mensaje_invalido = validar_solicitud(
            documento_identidad,
            valor_credito,
            plazo_meses,
            direccion_inmueble,
            documento_tipado=tipo_documento is not None,
        )
        if mensaje_invalido is not None:
            logger.info("oferta rechazada: solicitud invalida")
            return ErrorOfertaDatos(mensaje_invalido, puede_reintentar=False)

        # 0b. T19: con `placeId`, Places (detrás del ACL) normaliza la dirección. Un país no
        # soportado se rechaza; si Places no responde, sigue la dirección escrita «sin verificar».
        direccion_inmueble = await self._direccion(place_id, direccion_inmueble)
        if isinstance(direccion_inmueble, ErrorOfertaDatos):
            return direccion_inmueble

        # 1. Consentimiento ANTES de consultar cualquier fuente (regla #7, CA-W01-01/02/08).
        try:
            consentimiento = await self._clientes.auth.obtener_consentimiento(consentimiento_id)
        except ErrorServicio as error:
            logger.warning("AUTH no disponible error=%s", error)
            return ErrorOfertaDatos(
                MENSAJE_REINTENTAR, puede_reintentar=True, motivo=MOTIVO_FUENTES_NO_DISPONIBLES
            )
        if consentimiento is None or not consentimiento.get("vigente"):
            logger.info("oferta rechazada: sin consentimiento vigente")
            return ErrorOfertaDatos(
                MENSAJE_SIN_CONSENTIMIENTO,
                puede_reintentar=False,
                motivo=MOTIVO_CONSENTIMIENTO_NO_VIGENTE,
            )

        # 2. REWIRE: ver `cliente_id_desde_documento` (app/identificadores.py).
        # T20: el tipo y el país entran al titular (la cédula colombiana conserva el de siempre).
        cliente_id = cliente_id_desde_documento(
            documento_identidad, self._clave, pais, tipo_documento
        )
        titular_consentimiento = consentimiento.get("titular_id")
        if titular_consentimiento is not None and titular_consentimiento != cliente_id:
            logger.info("oferta rechazada: consentimiento de otro titular")
            return ErrorOfertaDatos(
                MENSAJE_SIN_CONSENTIMIENTO,
                puede_reintentar=False,
                motivo=MOTIVO_CONSENTIMIENTO_NO_VIGENTE,
            )

        # 3. Las fuentes configuradas en paralelo. El ACL Worker ya degrada solo (caché o
        # valor por defecto); aquí solo se cubre que el ACL mismo no responda.
        fuentes = await asyncio.gather(
            *(self._consultar_fuente(cliente_id, consentimiento_id, f) for f in FUENTES)
        )

        # 4. RISK escribe el perfil y devuelve `operation_time` (D-02).
        try:
            perfil = await self._clientes.risk.registrar_perfil(
                cliente_id, list(fuentes), consentimiento_id, consentimiento.get("vence_en")
            )
        except ErrorServicio as error:
            logger.warning("RISK no disponible cliente=%s error=%s", id_opaco(cliente_id), error)
            return ErrorOfertaDatos(
                MENSAJE_REINTENTAR, puede_reintentar=True, motivo=MOTIVO_FUENTES_NO_DISPONIBLES
            )

        # 5. RATING calcula la oferta leyendo el perfil con sesión causal.
        try:
            oferta = await self._clientes.rating.generar_oferta(
                cliente_id,
                perfil["operation_time"],
                valor_credito,
                plazo_meses,
                direccion_inmueble.strip(),
                consentimiento_id,
            )
        except DatosInsuficientes:
            # CA-W01-05: sin historial crediticio (ni en caché) no hay oferta posible.
            logger.info("oferta rechazada: datos insuficientes cliente=%s", id_opaco(cliente_id))
            return ErrorOfertaDatos(
                MENSAJE_DATOS_INSUFICIENTES,
                puede_reintentar=True,
                motivo=MOTIVO_DATOS_INSUFICIENTES,
            )
        except SolicitudInvalida:
            return ErrorOfertaDatos(MENSAJE_VALOR_INVALIDO, puede_reintentar=False)
        except ErrorServicio as error:
            logger.warning("RATING no disponible cliente=%s error=%s", id_opaco(cliente_id), error)
            return ErrorOfertaDatos(
                MENSAJE_REINTENTAR, puede_reintentar=True, motivo=MOTIVO_FUENTES_NO_DISPONIBLES
            )

        logger.info(
            "oferta entregada cliente=%s tipo=%s fuentes_degradadas=%d",
            id_opaco(cliente_id),
            oferta.get("tipo"),
            sum(1 for f in fuentes if f.get("degradado") or f.get("de_cache")),
        )
        return oferta

    async def _consentimiento_del_titular(
        self, consentimiento_id: str, cliente_id: str
    ) -> dict | ErrorOfertaDatos:
        """Regla 7: el consentimiento debe estar vigente y ser del titular, ANTES de consultar."""
        try:
            consentimiento = await self._clientes.auth.obtener_consentimiento(consentimiento_id)
        except ErrorServicio as error:
            logger.warning("AUTH no disponible error=%s", error)
            return ErrorOfertaDatos(
                MENSAJE_REINTENTAR, puede_reintentar=True, motivo=MOTIVO_FUENTES_NO_DISPONIBLES
            )
        titular = (consentimiento or {}).get("titular_id")
        if (
            not consentimiento
            or not consentimiento.get("vigente")
            or titular not in (None, cliente_id)
        ):
            logger.info("oferta rechazada: sin consentimiento vigente del titular")
            return ErrorOfertaDatos(
                MENSAJE_SIN_CONSENTIMIENTO,
                puede_reintentar=False,
                motivo=MOTIVO_CONSENTIMIENTO_NO_VIGENTE,
            )
        return consentimiento

    async def solicitar_auto(
        self,
        consentimiento_id: str,
        documento_identidad: str,
        placa: str,
        kilometraje_anual: int,
        ciudad: str,
        uso: str,
        deducible: str,
        tipo_documento: str | None = None,
        pais: str | None = None,
    ) -> dict | ErrorOfertaDatos:
        """KAN-290: la oferta de la póliza de auto. Mismo camino que la de vida: consentimiento
        primero (regla 7), las tres fuentes del vehículo por el ACL en paralelo, RISK escribe el
        perfil y RATING tarifa con sesión causal. La placa solo viaja al ACL; RISK y RATING
        reciben su HMAC (`vehiculo_id`). Ni la placa ni el documento se loguean (regla 6)."""
        placa_n = normalizar_placa(placa)
        if placa_n is None:
            return ErrorOfertaDatos(MENSAJE_PLACA_INVALIDA, puede_reintentar=False)
        if not 0 <= kilometraje_anual <= KILOMETRAJE_MAXIMO:
            return ErrorOfertaDatos(MENSAJE_KILOMETRAJE_INVALIDO, puede_reintentar=False)
        ciudad = (ciudad or "").strip()
        if not 2 <= len(ciudad) <= 60:
            return ErrorOfertaDatos(MENSAJE_CIUDAD_INVALIDA, puede_reintentar=False)

        cliente_id = cliente_id_desde_documento(
            documento_identidad, self._clave, pais, tipo_documento
        )
        consentimiento = await self._consentimiento_del_titular(consentimiento_id, cliente_id)
        if isinstance(consentimiento, ErrorOfertaDatos):
            return consentimiento

        vehiculo_id = vehiculo_id_desde_placa(placa_n, self._clave)
        fuentes = await asyncio.gather(
            *(
                self._consultar_vehiculo(vehiculo_id, placa_n, consentimiento_id, f)
                for f in FUENTES_VEHICULO
            )
        )
        try:
            perfil = await self._clientes.risk.registrar_perfil_vehiculo(
                vehiculo_id,
                cliente_id,
                consentimiento_id,
                list(fuentes),
                consentimiento.get("vence_en"),
            )
            oferta = await self._clientes.rating.generar_oferta_auto(
                {
                    "cliente_id": cliente_id,
                    "vehiculo_id": vehiculo_id,
                    "operation_time": perfil["operation_time"],
                    "consentimiento_id": consentimiento_id,
                    "placa_enmascarada": enmascarar_placa(placa_n),
                    "kilometraje_anual": kilometraje_anual,
                    "ciudad_circulacion": ciudad,
                    "uso": uso,
                    "deducible": deducible,
                }
            )
        except VehiculoRechazado as error:
            logger.info("oferta de auto rechazada motivo=%s", error.motivo)
            return ErrorOfertaDatos(error.mensaje, puede_reintentar=False, motivo=error.motivo)
        except DatosInsuficientes:
            return ErrorOfertaDatos(
                MENSAJE_DATOS_VEHICULO, puede_reintentar=True, motivo=MOTIVO_DATOS_INSUFICIENTES
            )
        except SolicitudInvalida:
            return ErrorOfertaDatos(MENSAJE_PLACA_INVALIDA, puede_reintentar=False)
        except ErrorServicio as error:
            logger.warning(
                "oferta de auto no disponible vehiculo=%s error=%s", id_opaco(vehiculo_id), error
            )
            return ErrorOfertaDatos(
                MENSAJE_REINTENTAR, puede_reintentar=True, motivo=MOTIVO_FUENTES_NO_DISPONIBLES
            )
        logger.info(
            "oferta de auto entregada vehiculo=%s tipo=%s",
            id_opaco(vehiculo_id),
            oferta.get("tipo"),
        )
        return oferta

    async def _consultar_vehiculo(
        self, vehiculo_id: str, placa: str, consentimiento_id: str, fuente: str
    ) -> dict:
        try:
            return await self._clientes.acl.consultar_vehiculo(
                vehiculo_id, placa, consentimiento_id, fuente
            )
        except ErrorServicio as error:
            logger.warning("ACL no disponible fuente=%s error=%s", fuente, error)
            return {
                "fuente": fuente,
                "datos": {},
                "capturado_en": datetime.now(UTC).isoformat(),
                "de_cache": False,
                "degradado": True,
            }

    async def registrar_consulta(
        self,
        actor_id: str,
        actor_rol: str,
        documento_identidad: str,
        consentimiento_id: str,
        oferta: dict,
        tipo_documento: str | None = None,
        pais: str | None = None,
    ) -> None:
        """QA 2026-11-10 (Q-08): deja en RISK el renglón del historial de fuentes de quien cotizó:
        el estado de cada fuente de la oferta entregada, nunca sus datos. Es un registro de apoyo:
        si RISK no responde, la oferta sigue (regla 4) y solo se anota en el log. El documento
        viaja enmascarado y el titular como su HMAC (regla 6)."""
        titular_id = cliente_id_desde_documento(
            documento_identidad, self._clave, pais, tipo_documento
        )
        try:
            await self._clientes.risk.registrar_consulta(
                {
                    "actor_id": actor_id,
                    "actor_rol": actor_rol,
                    "titular_id": titular_id,
                    "documento_enmascarado": enmascarar_documento(documento_identidad),
                    "consentimiento_id": consentimiento_id,
                    "tipo_oferta": oferta["tipo"],
                    "producto": oferta.get("producto") or "VIDA_DEUDOR",
                    "oferta_id": oferta.get("id"),
                    "fuentes": [
                        {
                            "id": f["id"],
                            "nombre": f["nombre"],
                            "origen": f["origen"],
                            "estado": f["estado"],
                            "capturado_en": f.get("capturadoEn"),
                        }
                        for f in oferta.get("fuentes", [])
                    ],
                }
            )
        except ErrorServicio as error:
            logger.warning(
                "RISK no registró la consulta cliente=%s error=%s", id_opaco(titular_id), error
            )

    async def _direccion(self, place_id: str | None, escrita: str) -> str | ErrorOfertaDatos:
        """La dirección con la que se cotiza: la normalizada por Places si el lugar se verificó,
        o la escrita («sin verificar») si no llegó `placeId` o Places no respondió. La dirección
        nunca se loguea (regla #6)."""
        if not place_id or not place_id.strip():
            return escrita
        try:
            lugar = await self._clientes.acl.validar_direccion(place_id.strip()[:300])
        except ErrorServicio:
            logger.info("oferta: dirección sin verificar (Places no respondió)")
            return escrita
        if not lugar.get("verificado"):
            logger.info("oferta: dirección sin verificar")
            return escrita
        if lugar.get("pais") not in {j.codigo for j in JURISDICCIONES}:
            logger.info("oferta rechazada: dirección en un país no soportado")
            return ErrorOfertaDatos(MENSAJE_DIRECCION_PAIS_NO_SOPORTADO, puede_reintentar=False)
        normalizada = (lugar.get("direccion") or "").strip()
        return normalizada[:MAX_LARGO_DIRECCION] if normalizada else escrita

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
