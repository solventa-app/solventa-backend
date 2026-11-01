"""Código de verificación de correo para cuentas del onboarding móvil (HU-M01).

Como en HU-W03 (`services/policy/app/application/servicio_firma_y_pago.py`), envía un código de 6
dígitos por el ACL Worker (`plantilla="codigo-verificacion"`), guarda solo su huella HMAC-SHA256
(en Redis con respaldo en memoria), vence a los 10 minutos, admite hasta 5 intentos y exige 60 s
entre envíos al mismo correo (`ESPERA_REQUERIDA`); un código nuevo invalida el anterior. Ni el
correo ni el código se escriben en los logs (regla #6)."""

import hashlib
import hmac
import logging
import math
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.clientes import Clientes, ErrorOnboardingServicio, ErrorServicio
from app.identificadores import id_opaco
from app.socios.modo_prueba import Almacen

logger = logging.getLogger("bff.codigo_onboarding")

LARGO_CODIGO = 6
VIGENCIA_MINUTOS = 10
MAX_INTENTOS = 5
ESPERA_ENTRE_ENVIOS_SEGUNDOS = 60  # mínimo entre dos envíos al mismo correo
CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PREFIJO_CLAVE = "onboarding:codigo:"


def _clave_huellas(material: str) -> bytes:
    return hashlib.sha256(b"onboarding-codigo:" + material.encode("utf-8")).digest()


def _huella(valor: str, clave: bytes) -> str:
    return hmac.new(clave, valor.encode("utf-8"), hashlib.sha256).hexdigest()


def _coincide(valor: str, huella_guardada: str, clave: bytes) -> bool:
    return hmac.compare_digest(_huella(valor, clave), huella_guardada)


def generar_codigo() -> str:
    return f"{secrets.randbelow(10**LARGO_CODIGO):0{LARGO_CODIGO}d}"


def enmascarar_correo(correo: str) -> str:
    """`ana.prueba@correo.com` -> `a•••@correo.com`: nunca el correo completo."""
    usuario, _, dominio = correo.partition("@")
    return f"{usuario[:1]}•••@{dominio}"


class ServicioCodigoOnboarding:
    def __init__(
        self,
        clientes: Clientes,
        almacen: Almacen,
        clave_material: str,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clientes = clientes
        self._almacen = almacen
        self._clave = _clave_huellas(clave_material)
        self._reloj = reloj

    def _normalizar_correo(self, correo: str) -> str:
        limpio = correo.strip().lower()
        if not CORREO_VALIDO.match(limpio) or len(limpio) > 254:
            raise ErrorOnboardingServicio("DATO_INVALIDO")
        return limpio

    def _id_correo(self, correo_limpio: str) -> str:
        return _huella(f"correo:{correo_limpio}", self._clave)

    async def enviar(self, correo: str) -> dict[str, str | datetime]:
        limpio = self._normalizar_correo(correo)
        try:
            if await self._clientes.auth.correo_registrado(limpio):
                # Antes de gastar un envío: con cuenta ya no se registra, se inicia sesión.
                raise ErrorOnboardingServicio("CORREO_YA_REGISTRADO")
        except ErrorServicio as error:
            raise ErrorOnboardingServicio("NO_DISPONIBLE") from error
        correo_id = self._id_correo(limpio)
        ahora = self._reloj()
        previo = await self._almacen.leer(PREFIJO_CLAVE + correo_id)
        if previo and previo.get("enviado_en"):
            transcurrido = (ahora - datetime.fromisoformat(previo["enviado_en"])).total_seconds()
            if 0 <= transcurrido < ESPERA_ENTRE_ENVIOS_SEGUNDOS:
                # Se valida en el servidor para que no se pueda saltar desde el cliente.
                restantes = math.ceil(ESPERA_ENTRE_ENVIOS_SEGUNDOS - transcurrido)
                raise ErrorOnboardingServicio("ESPERA_REQUERIDA", restantes)
        codigo = generar_codigo()
        vence_en = ahora + timedelta(minutes=VIGENCIA_MINUTOS)
        await self._almacen.guardar(
            PREFIJO_CLAVE + correo_id,
            {
                "huella": _huella(f"{correo_id}:{codigo}", self._clave),
                "vence_en": vence_en.isoformat(),
                "intentos": 0,
                "verificado": False,
                "enviado_en": ahora.isoformat(),
            },
        )
        try:
            resultado = await self._clientes.acl.enviar_notificacion(
                id_notificacion=f"codigo-onboarding-{uuid.uuid4()}",
                plantilla="codigo-verificacion",
                destinatario_id=correo_id[:24],
                datos={"codigo": codigo, "minutos": str(VIGENCIA_MINUTOS)},
                correo=limpio,
            )
        except ErrorServicio as error:
            raise ErrorOnboardingServicio("NO_DISPONIBLE") from error
        if resultado.get("estado") != "enviada":
            raise ErrorOnboardingServicio("NO_DISPONIBLE")
        logger.info("código de onboarding enviado correo_id=%s", id_opaco(correo_id))
        return {
            "correo_enmascarado": enmascarar_correo(limpio),
            "vence_en": vence_en,
        }

    async def verificar(self, correo: str, codigo: str) -> dict[str, str | datetime]:
        limpio = self._normalizar_correo(correo)
        correo_id = self._id_correo(limpio)
        clave_almacen = PREFIJO_CLAVE + correo_id
        registro = await self._almacen.leer(clave_almacen)
        if registro is None:
            raise ErrorOnboardingServicio("CODIGO_VENCIDO")
        if registro.get("verificado"):
            return {
                "correo_enmascarado": enmascarar_correo(limpio),
                "vence_en": datetime.fromisoformat(registro["vence_en"]),
            }
        intentos = int(registro.get("intentos", 0))
        if intentos >= MAX_INTENTOS:
            raise ErrorOnboardingServicio("INTENTOS_AGOTADOS")
        vence_en = datetime.fromisoformat(registro["vence_en"])
        if vence_en <= self._reloj():
            raise ErrorOnboardingServicio("CODIGO_VENCIDO")
        valido = _coincide(f"{correo_id}:{codigo.strip()}", str(registro["huella"]), self._clave)
        if not valido:
            intentos += 1
            registro["intentos"] = intentos
            await self._almacen.guardar(clave_almacen, registro)
            if intentos >= MAX_INTENTOS:
                raise ErrorOnboardingServicio("INTENTOS_AGOTADOS")
            raise ErrorOnboardingServicio("CODIGO_INVALIDO")
        registro["verificado"] = True
        await self._almacen.guardar(clave_almacen, registro)
        logger.info("correo de onboarding verificado correo_id=%s", id_opaco(correo_id))
        return {
            "correo_enmascarado": enmascarar_correo(limpio),
            "vence_en": vence_en,
        }
