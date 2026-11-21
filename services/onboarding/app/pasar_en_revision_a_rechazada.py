"""Script único (KAN-131): las sesiones que quedaron en EN_REVISION por una falla técnica pasan a
RECHAZADA con el mismo motivo, para que la app les muestre «Reintentar» sin gastar intento.

Antes de KAN-131, una caída de Didit o de su circuito cerraba la sesión en EN_REVISION, que no
tiene salida. Es un arreglo de datos, no de esquema (por eso no es una migración). Idempotente: una
segunda corrida no encuentra nada. Sin datos personales en la salida: solo cuántas pasaron.

    DATABASE_URL=... python -m app.pasar_en_revision_a_rechazada            # aplica
    DATABASE_URL=... python -m app.pasar_en_revision_a_rechazada --simular  # solo cuenta
"""

import argparse
import logging
import os

from app.application.repositorio import MOTIVOS_TECNICOS, construir_pool

logger = logging.getLogger("onboarding.pasar_en_revision")


def pasar(pool, simular: bool = False) -> int:  # noqa: ANN001 (ConnectionPool de psycopg)
    parametros = {"t": sorted(MOTIVOS_TECNICOS)}
    with pool.connection() as conexion, conexion.transaction():
        if simular:
            fila = conexion.execute(
                "SELECT count(*) AS total FROM sesiones_onboarding "
                "WHERE estado = 'EN_REVISION' AND motivo = ANY(%(t)s)",
                parametros,
            ).fetchone()
            return int(fila["total"]) if fila else 0
        filas = conexion.execute(
            """
            UPDATE sesiones_onboarding SET estado = 'RECHAZADA', actualizado_en = now()
            WHERE estado = 'EN_REVISION' AND motivo = ANY(%(t)s)
            RETURNING id, motivo
            """,
            parametros,
        ).fetchall()
        for fila in filas:
            conexion.execute(
                "INSERT INTO eventos_onboarding (sesion_id, tipo, detalle) "
                "VALUES (%(s)s, 'RECHAZADA', %(d)s)",
                {"s": fila["id"], "d": f"{fila['motivo']} (KAN-131, desde EN_REVISION)"},
            )
        return len(filas)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--simular", action="store_true", help="solo cuenta, no cambia nada")
    simular = argumentos.parse_args().simular
    pool = construir_pool(os.getenv("DATABASE_URL", ""))
    try:
        total = pasar(pool, simular)
    finally:
        pool.close()
    logger.info("%s sesiones %s", total, "por pasar" if simular else "pasadas a RECHAZADA")


if __name__ == "__main__":
    main()
