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

from app.clientes import Clientes, DatosInsuficientes, ErrorServicio, SolicitudInvalida
from app.identificadores import cliente_id_desde_documento, id_opaco

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
MENSAJE_VALOR_INVALIDO = "El valor del crédito no es válido."
MENSAJE_DOCUMENTO_INVALIDO = "El documento de identidad no es válido."
MENSAJE_PLAZO_INVALIDO = "El plazo del crédito debe estar entre 1 y 360 meses."
MENSAJE_DIRECCION_INVALIDA = "Ingresa la dirección del inmueble."
MENSAJE_DATOS_INSUFICIENTES = (
    "No pudimos consultar tu historial crediticio en este momento. "
    "Intenta de nuevo o habla con un asesor."
)

MOTIVO_CONSENTIMIENTO_NO_VIGENTE = "CONSENTIMIENTO_NO_VIGENTE"
MOTIVO_DATOS_INSUFICIENTES = "DATOS_INSUFICIENTES"
MOTIVO_FUENTES_NO_DISPONIBLES = "FUENTES_NO_DISPONIBLES"

# Mismos límites que el formulario W01-01 (solventa-frontend: SolicitudYConsentimiento.vue).
MIN_DIGITOS_DOCUMENTO = 6
MAX_LARGO_DOCUMENTO = 20
PLAZO_MINIMO_MESES = 1
PLAZO_MAXIMO_MESES = 360
MAX_LARGO_DIRECCION = 200


@dataclass
class ErrorOfertaDatos:
    mensaje: str
    puede_reintentar: bool
    motivo: str | None = None


def validar_solicitud(
    documento_identidad: str, valor_credito: str, plazo_meses: int, direccion_inmueble: str
) -> str | None:
    """Mensaje para el usuario si la solicitud no es válida, o `None`. Se valida antes de
    tocar el consentimiento o las fuentes: un dato mal escrito no gasta una consulta."""
    documento = documento_identidad.strip()
    digitos = sum(c.isdigit() for c in documento)
    if digitos < MIN_DIGITOS_DOCUMENTO or len(documento) > MAX_LARGO_DOCUMENTO:
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
    ) -> dict | ErrorOfertaDatos:
        """Devuelve la oferta tal como la entrega RATING (ya en el camelCase del
        contrato) o un `ErrorOfertaDatos`. El plazo y la dirección los usa RATING en la
        prima; ni el documento ni la dirección se loguean nunca (regla #6)."""
        # 0. Datos de la solicitud (B3): un error aquí no se arregla reintentando.
        mensaje_invalido = validar_solicitud(
            documento_identidad, valor_credito, plazo_meses, direccion_inmueble
        )
        if mensaje_invalido is not None:
            logger.info("oferta rechazada: solicitud invalida")
            return ErrorOfertaDatos(mensaje_invalido, puede_reintentar=False)

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
        cliente_id = cliente_id_desde_documento(documento_identidad, self._clave)
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
            perfil = await self._clientes.risk.registrar_perfil(cliente_id, list(fuentes))
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
