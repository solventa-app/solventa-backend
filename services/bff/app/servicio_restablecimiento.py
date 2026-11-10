"""«Olvidé mi contraseña» (KAN-26): enlace de un solo uso por correo, como el de la firma (W03).

Flujo: el visitante da solo su correo; si hay una cuenta ACTIVA con él, se genera un token de 256
bits, se guarda **solo su huella** y se envía el enlace `<URL_FRONT>/restablecer-clave#t=…` por el
ACL Worker. La respuesta al visitante es siempre la misma exista o no la cuenta, para no
revelar quién está registrado. El enlace vence a los 30 minutos, se usa una vez y uno nuevo
invalida el anterior. Tope de envíos: 3 por correo y 10 por IP por hora (se cuentan igual con o
sin cuenta). Al restablecer, AUTH cierra todas las sesiones de la cuenta. Ni el correo, ni el
token, ni la clave se escriben en los logs (regla #6)."""

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.clientes import Clientes, ErrorServicio, SolicitudInvalida
from app.identificadores import id_opaco
from app.socios.modo_prueba import Almacen

logger = logging.getLogger("bff.restablecimiento")

VIGENCIA_MINUTOS = 30
MAX_POR_CORREO_HORA = 3
MAX_POR_IP_HORA = 10
VENTANA = timedelta(hours=1)
CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PREFIJO = "restablecer:"


class EnlaceInvalido(Exception):
    """El enlace no existe, venció, ya se usó o la cuenta ya no está activa (mismo mensaje)."""


class ServicioRestablecimiento:
    def __init__(
        self,
        clientes: Clientes,
        almacen: Almacen,
        clave_material: str,
        url_front: str,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clientes = clientes
        self._almacen = almacen
        self._clave = hashlib.sha256(b"restablecer-clave:" + clave_material.encode()).digest()
        self._url_front = url_front.rstrip("/")
        self._reloj = reloj

    def _huella(self, valor: str) -> str:
        return hmac.new(self._clave, valor.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _dentro_del_tope(self, clave: str, maximo: int) -> bool:
        """Cuenta el intento y dice si todavía cabe en la última hora."""
        ahora = self._reloj()
        registro = await self._almacen.leer(PREFIJO + clave) or {"envios": []}
        recientes = [e for e in registro["envios"] if ahora - datetime.fromisoformat(e) < VENTANA]
        permitido = len(recientes) < maximo
        if permitido:
            recientes.append(ahora.isoformat())
        await self._almacen.guardar(PREFIJO + clave, {"envios": recientes})
        return permitido

    async def solicitar(self, correo: str, ip: str | None) -> None:
        """Nunca revela si el correo tiene cuenta: no lanza nada por «no existe» ni por el tope."""
        limpio = correo.strip().lower()
        if not CORREO_VALIDO.match(limpio) or len(limpio) > 254:
            return
        id_correo = self._huella(f"correo:{limpio}")
        if ip and not await self._dentro_del_tope(f"limite:ip:{self._huella(ip)}", MAX_POR_IP_HORA):
            logger.warning("restablecimiento: tope por IP")
            return
        if not await self._dentro_del_tope(f"limite:correo:{id_correo}", MAX_POR_CORREO_HORA):
            logger.warning("restablecimiento: tope por correo id=%s", id_opaco(id_correo))
            return
        try:
            usuario_id = await self._clientes.auth.buscar_para_restablecer(limpio)
            if usuario_id is None:
                return
            token = secrets.token_urlsafe(32)
            huella = self._huella(f"token:{token}")
            ahora = self._reloj()
            vence_en = ahora + timedelta(minutes=VIGENCIA_MINUTOS)
            # Un enlace nuevo invalida el anterior de la misma cuenta.
            llave_cuenta = f"cuenta:{self._huella(usuario_id)}"
            previo = await self._almacen.leer(PREFIJO + llave_cuenta)
            if previo and previo.get("huella"):
                anterior = await self._almacen.leer(PREFIJO + f"token:{previo['huella']}")
                if anterior:
                    await self._almacen.guardar(
                        PREFIJO + f"token:{previo['huella']}", {**anterior, "usado": True}
                    )
            await self._almacen.guardar(
                PREFIJO + f"token:{huella}",
                {"usuario_id": usuario_id, "vence_en": vence_en.isoformat(), "usado": False},
            )
            await self._almacen.guardar(PREFIJO + llave_cuenta, {"huella": huella})
            await self._clientes.acl.enviar_notificacion(
                id_notificacion=f"restablecer-clave-{uuid.uuid4()}",
                plantilla="restablecer-clave",
                destinatario_id=id_correo[:24],
                datos={
                    "enlace": f"{self._url_front}/restablecer-clave#t={token}",
                    "minutos": str(VIGENCIA_MINUTOS),
                },
                correo=limpio,
            )
            logger.info("enlace de restablecimiento enviado usuario=%s", id_opaco(usuario_id))
        except ErrorServicio:
            # El visitante no debe notar la falla (ni distinguirla de «no existe»).
            logger.warning("restablecimiento: no se pudo enviar el enlace")

    async def _registro_vigente(self, token: str | None) -> tuple[str, dict]:
        if not token:
            raise EnlaceInvalido
        llave = PREFIJO + f"token:{self._huella(f'token:{token}')}"
        registro = await self._almacen.leer(llave)
        if (
            registro is None
            or registro.get("usado")
            or datetime.fromisoformat(registro["vence_en"]) <= self._reloj()
        ):
            raise EnlaceInvalido
        return llave, registro

    async def enlace_vigente(self, token: str | None) -> bool:
        try:
            await self._registro_vigente(token)
        except EnlaceInvalido:
            return False
        return True

    async def restablecer(self, token: str | None, clave: str) -> None:
        """`EnlaceInvalido` si no sirve; `SolicitudInvalida` (con el mensaje) si la clave no
        cumple las reglas, y en ese caso el enlace **no** se gasta."""
        llave, registro = await self._registro_vigente(token)
        try:
            correo = await self._clientes.auth.restablecer_clave(registro["usuario_id"], clave)
        except SolicitudInvalida:
            raise
        if correo is None:
            raise EnlaceInvalido
        await self._almacen.guardar(llave, {**registro, "usado": True})
        logger.info("clave restablecida usuario=%s", id_opaco(registro["usuario_id"]))
        try:
            await self._clientes.acl.enviar_notificacion(
                id_notificacion=f"clave-cambiada-{uuid.uuid4()}",
                plantilla="clave-cambiada",
                destinatario_id=self._huella(f"correo:{correo.lower()}")[:24],
                datos={},
                correo=correo,
            )
        except ErrorServicio:
            logger.warning("restablecimiento: no se pudo enviar el aviso de cambio")
