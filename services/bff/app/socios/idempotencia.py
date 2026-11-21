"""Idempotencia de las cotizaciones de socios (`Idempotency-Key`, contrato v0.54, KAN-30).

El gateway corta a los 700 ms (W07-25), pero el BFF termina la cotización: reserva presupuesto y
la registra como `OK` aunque el socio vio un 504. Si el socio reintenta con la misma
`Idempotency-Key`, recibe la misma respuesta guardada sin reservar ni registrar otra vez.

- La clave vale por socio, entorno y API: dos socios pueden usar la misma sin cruzarse.
- Mientras la primera está en curso, un reintento responde 409 `SOLICITUD_EN_CURSO` (Retry-After).
- La misma clave con otro cuerpo responde 422 `IDEMPOTENCIA_EN_CONFLICTO`.
- Solo se guarda la respuesta exitosa. Si la cotización falla, la clave se libera (el presupuesto
  ya se devolvió) y el reintento vuelve a intentarlo.
- Sin la cabecera, todo sigue como antes. Si Redis no responde, la cotización sigue sin
  idempotencia (regla 4): mejor un posible doble que bloquear al socio.

No se guardan datos personales en la clave: el cuerpo entra solo como huella SHA-256. La respuesta
guardada es la oferta que el socio ya recibió (sin el documento del titular)."""

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request

from app.socios.errores import ErrorApiSocio

logger = logging.getLogger("bff.socios.idempotencia")

CABECERA = "idempotency-key"
CABECERA_REPETIDA = "Idempotent-Replayed"
TTL_EN_CURSO_SEGUNDOS = 30
TTL_HECHA_SEGUNDOS = 24 * 3600
SEGUNDOS_EN_CURSO = 1
_CLAVE_VALIDA = re.compile(r"^[\x21-\x7e]{1,255}$")

MENSAJE_CLAVE_INVALIDA = "La cabecera Idempotency-Key debe tener entre 1 y 255 caracteres visibles."
MENSAJE_EN_CURSO = "Ya hay una solicitud en curso con esta Idempotency-Key. Intenta de nuevo."
MENSAJE_CONFLICTO = "Esta Idempotency-Key ya se usó con otros datos."


class AlmacenIdempotencia(Protocol):
    async def reclamar(self, clave: str, registro: dict, ttl: int) -> dict | None:
        """Guarda `registro` si la clave no existe y devuelve `None`; si existe, devuelve el
        registro guardado sin tocarlo."""
        ...

    async def completar(self, clave: str, registro: dict, ttl: int) -> None: ...

    async def liberar(self, clave: str) -> None: ...


class AlmacenIdempotenciaMemoria:
    """Para pruebas (sin vencimiento)."""

    def __init__(self) -> None:
        self.datos: dict[str, str] = {}

    async def reclamar(self, clave: str, registro: dict, ttl: int) -> dict | None:
        if clave in self.datos:
            return json.loads(self.datos[clave])
        self.datos[clave] = json.dumps(registro)
        return None

    async def completar(self, clave: str, registro: dict, ttl: int) -> None:
        self.datos[clave] = json.dumps(registro)

    async def liberar(self, clave: str) -> None:
        self.datos.pop(clave, None)


class AlmacenIdempotenciaRedis:
    def __init__(self, url: str) -> None:
        import redis.asyncio  # solo si hay Redis configurado

        self._redis: Any = redis.asyncio.Redis.from_url(
            url, socket_timeout=0.2, socket_connect_timeout=0.2
        )

    async def reclamar(self, clave: str, registro: dict, ttl: int) -> dict | None:
        if await self._redis.set(clave, json.dumps(registro), nx=True, ex=ttl):
            return None
        valor = await self._redis.get(clave)
        # Venció entre el SET y el GET: se trata como en curso para no duplicar.
        return json.loads(valor) if valor else {"estado": "EN_CURSO"}

    async def completar(self, clave: str, registro: dict, ttl: int) -> None:
        await self._redis.set(clave, json.dumps(registro), ex=ttl)

    async def liberar(self, clave: str) -> None:
        await self._redis.delete(clave)

    async def aclose(self) -> None:
        await self._redis.aclose()


@dataclass
class Repetida:
    """Una respuesta ya entregada: el middleware no la vuelve a registrar en el consumo."""

    cuerpo: dict


def huella(ruta: str, cuerpo: dict) -> str:
    texto = json.dumps({"ruta": ruta, "cuerpo": cuerpo}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode()).hexdigest()


async def ejecutar_idempotente(
    request: Request,
    almacen: AlmacenIdempotencia | None,
    socio_id: str,
    entorno: str,
    api: str,
    cuerpo: dict,
    operacion: Callable[[], Awaitable[dict]],
) -> dict | Repetida:
    """Corre `operacion` una sola vez por `Idempotency-Key`. Sin cabecera o sin almacén, la corre
    sin más."""
    llave = request.headers.get(CABECERA)
    if llave is None or almacen is None:
        return await operacion()
    if not _CLAVE_VALIDA.fullmatch(llave):
        raise ErrorApiSocio(422, "DATOS_INVALIDOS", MENSAJE_CLAVE_INVALIDA)
    clave = f"socios:idem:{socio_id}:{entorno}:{api}:{hashlib.sha256(llave.encode()).hexdigest()}"
    marca = huella(request.url.path, cuerpo)
    try:
        previo = await almacen.reclamar(
            clave, {"estado": "EN_CURSO", "huella": marca}, TTL_EN_CURSO_SEGUNDOS
        )
    except Exception:  # noqa: BLE001 - Redis caído: se cotiza sin idempotencia (regla 4)
        logger.warning("idempotencia no disponible: se cotiza sin ella")
        return await operacion()
    if previo is not None:
        if previo.get("huella") not in (None, marca):
            raise ErrorApiSocio(422, "IDEMPOTENCIA_EN_CONFLICTO", MENSAJE_CONFLICTO)
        if previo.get("estado") == "HECHA":
            return Repetida(previo["respuesta"])
        raise ErrorApiSocio(
            409, "SOLICITUD_EN_CURSO", MENSAJE_EN_CURSO, True, None, SEGUNDOS_EN_CURSO
        )
    try:
        respuesta = await operacion()
    except BaseException:
        # Falló (o se canceló): el presupuesto ya volvió y el reintento debe poder cotizar.
        try:
            await almacen.liberar(clave)
        except Exception:  # noqa: BLE001 - vence sola a los TTL_EN_CURSO_SEGUNDOS
            logger.warning("no se pudo liberar una Idempotency-Key")
        raise
    try:
        await almacen.completar(
            clave, {"estado": "HECHA", "huella": marca, "respuesta": respuesta}, TTL_HECHA_SEGUNDOS
        )
    except Exception:  # noqa: BLE001 - la cotización ya se hizo: se entrega igual
        logger.warning("no se pudo guardar la respuesta de una Idempotency-Key")
    return respuesta
