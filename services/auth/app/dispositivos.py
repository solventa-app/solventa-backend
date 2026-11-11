"""Dispositivos de confianza (KAN-26): entrar con huella o rostro aunque la sesión se haya cerrado.

El teléfono genera un par EC P-256 en su Keystore/Keychain (la privada exige biometría y no sale
de ahí) y registra la pública estando con sesión. Para entrar sin escribir la clave:
1. `reto(dispositivo_id)`: AUTH entrega `id.nonce.exp.mac`, firmado con HMAC y de 60 s de vida.
2. La app firma ese texto (ECDSA SHA-256, DER en base64) tras la biometría.
3. `iniciar_sesion(...)` verifica el HMAC, el vencimiento y la firma, y emite la sesión de la app.

Un solo dispositivo activo por cuenta (registrar otro revoca el anterior); cambiar la clave, o
suspender la cuenta, los invalida. Ni la clave pública ni los retos se escriben en los logs."""

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg_pool import ConnectionPool

from app.identificadores import id_opaco

logger = logging.getLogger("auth.dispositivos")

VIGENCIA_RETO = timedelta(seconds=60)


class DispositivoInvalido(Exception):
    """Llave mal formada, reto inválido o vencido, firma incorrecta o dispositivo revocado.
    Mismo mensaje siempre: no se dice cuál fue (nadie aprende si un dispositivo existe)."""


MENSAJE_DISPOSITIVO = "No pudimos verificar este dispositivo. Ingresa con tu correo y contraseña."


class RepositorioDispositivos(Protocol):
    def registrar(self, dispositivo_id: str, usuario_id: str, clave_publica: str) -> None:
        """Revoca los activos de la cuenta y guarda el nuevo."""
        ...

    def obtener_activo(self, dispositivo_id: str) -> dict | None: ...

    def marcar_uso(self, dispositivo_id: str, ahora: datetime) -> None: ...

    def revocar_de_usuario(self, usuario_id: str, ahora: datetime) -> None: ...


class RepositorioDispositivosPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def registrar(self, dispositivo_id: str, usuario_id: str, clave_publica: str) -> None:
        with self._pool.connection() as conexion, conexion.transaction():
            conexion.execute(
                "UPDATE dispositivos SET revocado_en = now() "
                "WHERE usuario_id = %(u)s AND revocado_en IS NULL",
                {"u": usuario_id},
            )
            conexion.execute(
                "INSERT INTO dispositivos (id, usuario_id, clave_publica) "
                "VALUES (%(i)s, %(u)s, %(k)s)",
                {"i": dispositivo_id, "u": usuario_id, "k": clave_publica},
            )

    def obtener_activo(self, dispositivo_id: str) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                "SELECT id, usuario_id, clave_publica FROM dispositivos "
                "WHERE id = %(i)s AND revocado_en IS NULL",
                {"i": dispositivo_id},
            ).fetchone()
        return {"id": fila[0], "usuario_id": fila[1], "clave_publica": fila[2]} if fila else None

    def marcar_uso(self, dispositivo_id: str, ahora: datetime) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE dispositivos SET ultimo_uso = %(a)s WHERE id = %(i)s",
                {"a": ahora, "i": dispositivo_id},
            )

    def revocar_de_usuario(self, usuario_id: str, ahora: datetime) -> None:
        with self._pool.connection() as conexion:
            conexion.execute(
                "UPDATE dispositivos SET revocado_en = %(a)s "
                "WHERE usuario_id = %(u)s AND revocado_en IS NULL",
                {"a": ahora, "u": usuario_id},
            )


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _cargar_clave(clave_publica: str) -> ec.EllipticCurvePublicKey:
    """SPKI (DER) en base64, de una curva P-256; cualquier otra cosa se rechaza."""
    try:
        clave = serialization.load_der_public_key(base64.b64decode(clave_publica, validate=True))
    except (ValueError, TypeError) as error:
        raise DispositivoInvalido(MENSAJE_DISPOSITIVO) from error
    if not isinstance(clave, ec.EllipticCurvePublicKey) or clave.curve.name != "secp256r1":
        raise DispositivoInvalido(MENSAJE_DISPOSITIVO)
    return clave


class ServicioDispositivos:
    def __init__(
        self,
        repositorio: RepositorioDispositivos,
        clave_reto: str,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._clave = hashlib.sha256(b"reto-dispositivo:" + clave_reto.encode()).digest()
        self._reloj = reloj

    def registrar(self, usuario_id: str, clave_publica: str) -> str:
        _cargar_clave(clave_publica)  # valida antes de guardar
        dispositivo_id = str(uuid.uuid4())
        self._repo.registrar(dispositivo_id, usuario_id, clave_publica)
        logger.info("dispositivo registrado usuario=%s", id_opaco(usuario_id))
        return dispositivo_id

    def _mac(self, texto: str) -> str:
        return _b64(hmac.new(self._clave, texto.encode(), hashlib.sha256).digest())

    def reto(self, dispositivo_id: str) -> tuple[str, datetime]:
        """Siempre entrega un reto, exista o no el dispositivo: no revela cuáles existen."""
        vence = self._reloj() + VIGENCIA_RETO
        cuerpo = f"{dispositivo_id}.{secrets.token_urlsafe(24)}.{int(vence.timestamp())}"
        return f"{cuerpo}.{self._mac(cuerpo)}", vence

    def verificar(self, dispositivo_id: str, reto: str, firma: str) -> str:
        """Devuelve el `usuario_id` si el reto es de AUTH, sigue vigente y la firma es del
        dispositivo. Cualquier otra cosa: `DispositivoInvalido`."""
        partes = reto.split(".")
        if len(partes) != 4 or partes[0] != dispositivo_id:
            raise DispositivoInvalido(MENSAJE_DISPOSITIVO)
        cuerpo = ".".join(partes[:3])
        if not hmac.compare_digest(self._mac(cuerpo), partes[3]):
            raise DispositivoInvalido(MENSAJE_DISPOSITIVO)
        try:
            vence = int(partes[2])
        except ValueError as error:
            raise DispositivoInvalido(MENSAJE_DISPOSITIVO) from error
        ahora = self._reloj()
        if vence <= ahora.timestamp():
            raise DispositivoInvalido(MENSAJE_DISPOSITIVO)
        fila = self._repo.obtener_activo(dispositivo_id)
        if fila is None:
            raise DispositivoInvalido(MENSAJE_DISPOSITIVO)
        try:
            _cargar_clave(fila["clave_publica"]).verify(
                base64.b64decode(firma), reto.encode(), ec.ECDSA(hashes.SHA256())
            )
        except (InvalidSignature, ValueError) as error:
            logger.info("firma de dispositivo rechazada usuario=%s", id_opaco(fila["usuario_id"]))
            raise DispositivoInvalido(MENSAJE_DISPOSITIVO) from error
        self._repo.marcar_uso(dispositivo_id, ahora)
        return fila["usuario_id"]

    def revocar_de_usuario(self, usuario_id: str) -> None:
        self._repo.revocar_de_usuario(usuario_id, self._reloj())
