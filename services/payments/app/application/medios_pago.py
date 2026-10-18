"""Medios de pago de los clientes (KAN-208): tarjetas tokenizadas por la pasarela.

PAYMENTS es el único escritor de `medios_pago` (regla #1). Aquí **nunca** hay un número de
tarjeta: la tarjeta se tokeniza en la pasarela (en la demo, el mock de `stubs/pasarela`) y
solo llega el token opaco, la marca y los últimos 4 dígitos. El token va **cifrado por campo**
(`app/application/cifrado.py`) y nunca sale de este servicio: ni en las respuestas ni en los
logs; solo se usa para cobrar. El `id` del medio es el dato asociado del cifrado: copiar el
cifrado de una fila a otra lo vuelve ilegible.

Capa simple (regla #3), con un `Protocol` para probar sin PostgreSQL."""

import hashlib
import logging
import uuid
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos, ErrorDeCifrado
from app.application.identificadores import id_opaco

logger = logging.getLogger("payments.medios_pago")

_PUBLICAS = "id, cliente_id, marca, ultimos4, vencimiento_mes, vencimiento_anio, creado_en"


class MedioPagoInvalido(Exception):
    """Datos del medio de pago inválidos (400 en la API). Mensaje apto para quien llama."""


class MedioPagoNoUtilizable(Exception):
    """El medio de pago no existe o no es del cliente del cobro (409 en la API)."""


def huella_de(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RepositorioMediosPago(Protocol):
    def crear_o_obtener(self, medio: dict[str, Any]) -> dict | None: ...

    def obtener_por_huella(self, cliente_id: str, huella: str) -> dict | None: ...

    def obtener(self, medio_pago_id: str) -> dict | None: ...

    def listar_de_cliente(self, cliente_id: str) -> list[dict]: ...

    def resumenes(self, ids: list[str]) -> dict[str, dict]: ...


class RepositorioMediosPagoPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _una(self, sql: str, parametros: dict) -> dict | None:
        with self._pool.connection() as conexion:
            fila = conexion.execute(sql, parametros).fetchone()
            return dict(fila) if fila is not None else None

    def crear_o_obtener(self, medio: dict[str, Any]) -> dict | None:
        """`None` si el cliente ya tenía esa tarjeta (índice único: correcto bajo concurrencia)."""
        return self._una(
            f"""
            INSERT INTO medios_pago
                (id, cliente_id, token_cifrado, token_huella, marca, ultimos4,
                 vencimiento_mes, vencimiento_anio)
            VALUES
                (%(id)s, %(cliente_id)s, %(token_cifrado)s, %(token_huella)s, %(marca)s,
                 %(ultimos4)s, %(vencimiento_mes)s, %(vencimiento_anio)s)
            ON CONFLICT (cliente_id, token_huella) DO NOTHING
            RETURNING {_PUBLICAS}
            """,
            medio,
        )

    def obtener_por_huella(self, cliente_id: str, huella: str) -> dict | None:
        return self._una(
            f"""
            SELECT {_PUBLICAS} FROM medios_pago
            WHERE cliente_id = %(cliente_id)s AND token_huella = %(huella)s
            """,
            {"cliente_id": cliente_id, "huella": huella},
        )

    def obtener(self, medio_pago_id: str) -> dict | None:
        """Incluye `token_cifrado`: solo para cobrar, nunca para responder."""
        return self._una(
            f"SELECT {_PUBLICAS}, token_cifrado FROM medios_pago WHERE id = %(id)s",
            {"id": medio_pago_id},
        )

    def listar_de_cliente(self, cliente_id: str) -> list[dict]:
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                f"""
                SELECT {_PUBLICAS} FROM medios_pago
                WHERE cliente_id = %(cliente_id)s ORDER BY creado_en
                """,
                {"cliente_id": cliente_id},
            ).fetchall()
            return [dict(fila) for fila in filas]

    def resumenes(self, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        with self._pool.connection() as conexion:
            filas = conexion.execute(
                "SELECT id, marca, ultimos4 FROM medios_pago WHERE id = ANY(%(ids)s)",
                {"ids": ids},
            ).fetchall()
            return {f["id"]: {"marca": f["marca"], "ultimos4": f["ultimos4"]} for f in filas}


def _validar(marca: str, ultimos4: str, mes: int, anio: int) -> None:
    if not marca.strip() or len(marca) > 20:
        raise MedioPagoInvalido("La marca de la tarjeta no es válida.")
    if len(ultimos4) != 4 or not ultimos4.isdigit():
        raise MedioPagoInvalido("Los últimos 4 dígitos deben ser exactamente 4 números.")
    if not 1 <= mes <= 12:
        raise MedioPagoInvalido("El mes de vencimiento debe estar entre 1 y 12.")
    if not 2000 <= anio <= 2100:
        raise MedioPagoInvalido("El año de vencimiento no es válido.")


class ServicioMediosPago:
    def __init__(self, repositorio: RepositorioMediosPago, cifrador: CifradorCampos) -> None:
        self._repositorio = repositorio
        self._cifrador = cifrador

    def registrar(
        self,
        cliente_id: str,
        token: str,
        marca: str,
        ultimos4: str,
        vencimiento_mes: int,
        vencimiento_anio: int,
    ) -> dict:
        """Idempotente: registrar dos veces la misma tarjeta de un cliente devuelve el mismo
        medio de pago."""
        _validar(marca, ultimos4, vencimiento_mes, vencimiento_anio)
        medio_pago_id = str(uuid.uuid4())
        huella = huella_de(token)
        creado = self._repositorio.crear_o_obtener(
            {
                "id": medio_pago_id,
                "cliente_id": cliente_id,
                "token_cifrado": self._cifrador.cifrar(token, medio_pago_id),
                "token_huella": huella,
                "marca": marca.strip().lower(),
                "ultimos4": ultimos4,
                "vencimiento_mes": vencimiento_mes,
                "vencimiento_anio": vencimiento_anio,
            }
        )
        if creado is None:
            return self._repositorio.obtener_por_huella(cliente_id, huella)
        logger.info("medio de pago registrado id=%s", id_opaco(creado["id"]))
        return creado

    def listar_de_cliente(self, cliente_id: str) -> list[dict]:
        return self._repositorio.listar_de_cliente(cliente_id)

    def token_para_cobrar(self, medio_pago_id: str, cliente_id: str | None) -> str:
        """El token en claro, solo para cobrar. Valida que el medio exista y sea del cliente
        al que se le cobra: sin esto, cualquiera podría cobrar con la tarjeta de otro."""
        if not cliente_id:
            raise MedioPagoNoUtilizable(
                "Un cobro con un medio de pago registrado debe indicar el cliente."
            )
        medio = self._repositorio.obtener(medio_pago_id)
        if medio is None or medio["cliente_id"] != cliente_id:
            # El mismo mensaje en ambos casos: no se revela si existe el medio de otro cliente.
            raise MedioPagoNoUtilizable("Ese medio de pago no existe o no es de este cliente.")
        try:
            return self._cifrador.descifrar(medio["token_cifrado"], medio["id"])
        except ErrorDeCifrado as error:
            raise MedioPagoNoUtilizable("No se pudo usar ese medio de pago.") from error

    def resumenes(self, ids: list[str]) -> dict[str, dict]:
        return self._repositorio.resumenes(sorted(set(ids)))
