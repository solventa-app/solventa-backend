"""Orden de pago de la indemnización de un siniestro aprobado (HU-W04, W04-6).

PAYMENTS es el escritor único de los pagos: CLAIMS le pide la orden al aprobar. Es solo la orden
(`PENDIENTE_DISPERSION`); la dispersión por la pasarela llega en el Sprint 3 (CA-W05-07). La orden
es idempotente por `siniestro_id`: repetirla con los mismos datos devuelve la misma; con otros datos
es un conflicto (nunca se cambia el monto de una orden ya creada).
"""

import logging
import re
import uuid
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

from app.application.identificadores import id_opaco

logger = logging.getLogger(__name__)

METODO_TRANSFERENCIA = "ACH Colombia Interbancario"
MONTO = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_COLUMNAS = (
    "id, siniestro_id, poliza_id, monto, moneda, estado, metodo_transferencia, creado_en, "
    "actualizado_en"
)


class OrdenInvalida(Exception):
    """Datos de la orden fuera de las reglas (monto, moneda)."""


class OrdenEnConflicto(Exception):
    """Ya hay una orden para ese siniestro con otros datos."""


class PuertoRepositorioIndemnizaciones(Protocol):
    def crear_o_obtener(self, datos: dict[str, Any]) -> dict[str, Any]: ...

    def obtener(self, indemnizacion_id: str) -> dict[str, Any] | None: ...


class RepositorioIndemnizacionesPostgres:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def crear_o_obtener(self, datos: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                f"""
                INSERT INTO indemnizaciones
                    (id, siniestro_id, poliza_id, monto, moneda, metodo_transferencia)
                VALUES
                    (%(id)s, %(siniestro_id)s, %(poliza_id)s, %(monto)s, %(moneda)s,
                     %(metodo_transferencia)s)
                ON CONFLICT (siniestro_id) DO NOTHING
                RETURNING {_COLUMNAS}
                """,
                {"id": str(uuid.uuid4()), **datos},
            ).fetchone()
            if fila is not None:
                return fila
            return conexion.execute(
                f"SELECT {_COLUMNAS} FROM indemnizaciones WHERE siniestro_id = %(siniestro_id)s",
                {"siniestro_id": datos["siniestro_id"]},
            ).fetchone()

    def obtener(self, indemnizacion_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conexion:
            return conexion.execute(
                f"SELECT {_COLUMNAS} FROM indemnizaciones WHERE id = %(id)s",
                {"id": indemnizacion_id},
            ).fetchone()


class ServicioIndemnizaciones:
    def __init__(self, repositorio: PuertoRepositorioIndemnizaciones) -> None:
        self._repo = repositorio

    def ordenar(self, datos: dict[str, Any]) -> dict[str, Any]:
        if not MONTO.match(datos["monto"]) or float(datos["monto"]) <= 0:
            raise OrdenInvalida("El monto de la indemnización debe ser un decimal positivo.")
        if not re.match(r"^[A-Z]{3}$", datos["moneda"]):
            raise OrdenInvalida("La moneda debe ser un código ISO de 3 letras.")
        pedida = {**datos, "metodo_transferencia": METODO_TRANSFERENCIA}
        orden = self._repo.crear_o_obtener(pedida)
        if (orden["poliza_id"], orden["monto"], orden["moneda"]) != (
            datos["poliza_id"],
            datos["monto"],
            datos["moneda"],
        ):
            raise OrdenEnConflicto("Ya hay una orden de indemnización para ese siniestro.")
        logger.info(
            "indemnizacion=%s siniestro=%s estado=%s",
            id_opaco(orden["id"]),
            id_opaco(orden["siniestro_id"]),
            orden["estado"],
        )
        return orden

    def obtener(self, indemnizacion_id: str) -> dict[str, Any] | None:
        return self._repo.obtener(indemnizacion_id)
