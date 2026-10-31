"""Cobro recurrente de las pólizas (HU-W05, W05-A7).

POLICY pide la programación al emitir. Sale del primer cobro de la propuesta, el `INTERACTIVO` que
dejó un medio tokenizado; sin él (cobros con el token del convenio) no hay nada que programar.

Cada pasada de `POST /cobros/_generar-recurrentes` reserva en **una transacción** el ciclo vencido y
crea su cobro en `cobrando`. La pasarela se llama después, fuera de la transacción, para no retener
el bloqueo. Si el proceso se cae entre los dos pasos, el ciclo no se pierde: el cobro queda atascado
y lo recupera `ReintentoCobrosPendientes`. Desde ahí sigue el flujo de W05: reintento, conciliación
y la tabla del back-office. `FOR UPDATE SKIP LOCKED` evita que dos pasadas tomen la misma fila, y el
`id_intento_cobro` determinista evita cobrar dos veces el mismo ciclo."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

from app.application.cifrado import CifradorCampos
from app.application.identificadores import id_opaco
from app.application.repositorio_cobros import _COLUMNAS

logger = logging.getLogger("payments.cobro_recurrente")

ESPACIO_CICLOS = uuid.UUID("6f1d4c52-8e0b-4a57-9d3e-2b7c1a90e4f3")
LOTE_RECURRENTES = 100


def id_intento_ciclo(propuesta_id: str, ciclo: int) -> str:
    return str(uuid.uuid5(ESPACIO_CICLOS, f"{propuesta_id}:{ciclo}"))


class RepositorioProgramacionesPostgres:
    def __init__(self, pool: ConnectionPool, cifrador: CifradorCampos | None = None) -> None:
        self._pool = pool
        self._cifrador = cifrador

    def crear(
        self, propuesta_id: str, poliza_id: str, vigencia_hasta: datetime, intervalo_dias: int
    ) -> bool:
        """Idempotente por propuesta. `False` si ya existía o si no hay medio guardado."""
        with self._pool.connection() as conexion:
            fila = conexion.execute(
                """
                INSERT INTO programacion_cobros
                    (id, propuesta_id, poliza_id, cliente_id, medio_pago_id, monto, moneda,
                     intervalo_dias, proximo_cobro_en, vigencia_hasta, activa)
                SELECT %(id)s, %(propuesta_id)s, %(poliza_id)s, c.cliente_id, c.medio_pago_id,
                       c.monto, c.moneda, %(intervalo)s,
                       c.creado_en + make_interval(days => %(intervalo)s),
                       %(vigencia)s,
                       c.creado_en + make_interval(days => %(intervalo)s) <= %(vigencia)s
                FROM cobros c
                WHERE c.poliza_id = %(propuesta_id)s AND c.origen = 'INTERACTIVO'
                  AND c.estado = 'cobrado' AND c.medio_pago_id IS NOT NULL
                ORDER BY c.creado_en DESC
                LIMIT 1
                ON CONFLICT (propuesta_id) DO NOTHING
                RETURNING id
                """,
                {
                    "id": str(uuid.uuid4()),
                    "propuesta_id": propuesta_id,
                    "poliza_id": poliza_id,
                    "intervalo": intervalo_dias,
                    "vigencia": vigencia_hasta,
                },
            ).fetchone()
        return fila is not None

    def reservar_ciclos(self, ahora: datetime, lote: int) -> list[dict[str, Any]]:
        """Avanza las programaciones vencidas y crea el cobro de cada una, todo o nada."""
        cobros = []
        with self._pool.connection() as conexion, conexion.transaction():
            vencidas = conexion.execute(
                """
                SELECT p.*, m.token_cifrado
                FROM programacion_cobros p JOIN medios_pago m ON m.id = p.medio_pago_id
                WHERE p.activa AND p.proximo_cobro_en <= %(ahora)s
                ORDER BY p.proximo_cobro_en
                LIMIT %(lote)s
                FOR UPDATE OF p SKIP LOCKED
                """,
                {"ahora": ahora, "lote": lote},
            ).fetchall()
            for programacion in vencidas:
                token = self._descifrar(
                    programacion["token_cifrado"], programacion["medio_pago_id"]
                )
                ciclo = programacion["ciclo"] + 1
                proximo = programacion["proximo_cobro_en"] + timedelta(
                    days=programacion["intervalo_dias"]
                )
                cobro_id = str(uuid.uuid4())
                conexion.execute(
                    """
                    UPDATE programacion_cobros
                    SET ciclo = %(ciclo)s, proximo_cobro_en = %(proximo)s,
                        activa = %(activa)s, actualizado_en = now()
                    WHERE id = %(id)s
                    """,
                    {
                        "id": programacion["id"],
                        "ciclo": ciclo,
                        "proximo": proximo,
                        "activa": proximo <= programacion["vigencia_hasta"],
                    },
                )
                cobro = conexion.execute(
                    f"""
                    INSERT INTO cobros
                        (id, id_intento_cobro, poliza_id, cliente_id, monto, moneda,
                         token_medio_pago, estado, origen, medio_pago_id)
                    VALUES
                        (%(id)s, %(intento)s, %(poliza_id)s, %(cliente_id)s, %(monto)s, %(moneda)s,
                         %(token)s, 'cobrando', 'RECURRENTE', %(medio)s)
                    ON CONFLICT (id_intento_cobro) DO NOTHING
                    RETURNING {_COLUMNAS}
                    """,
                    {
                        "id": cobro_id,
                        "intento": id_intento_ciclo(programacion["propuesta_id"], ciclo),
                        # Como el primer cobro: la convención de POLICY es el id de la propuesta.
                        "poliza_id": programacion["propuesta_id"],
                        "cliente_id": programacion["cliente_id"],
                        "monto": programacion["monto"],
                        "moneda": programacion["moneda"],
                        "token": self._cifrar(token, cobro_id),
                        "medio": programacion["medio_pago_id"],
                    },
                ).fetchone()
                if cobro is not None:
                    cobros.append({**cobro, "token_medio_pago": token})
        return cobros

    def _cifrar(self, texto: str, dueno: str) -> str:
        return self._cifrador.cifrar(texto, dueno) if self._cifrador else texto

    def _descifrar(self, texto: str, dueno: str) -> str:
        return self._cifrador.descifrar(texto, dueno) if self._cifrador else texto


class RepositorioProgramaciones(Protocol):
    def crear(
        self, propuesta_id: str, poliza_id: str, vigencia_hasta: datetime, intervalo_dias: int
    ) -> bool: ...

    def reservar_ciclos(self, ahora: datetime, lote: int) -> list[dict[str, Any]]: ...


class PuertoEjecutorCobro(Protocol):
    def ejecutar_cobro(self, cobro: dict[str, Any]) -> dict[str, Any]: ...


class ServicioCobroRecurrente:
    def __init__(
        self,
        repositorio: RepositorioProgramaciones,
        cobros: PuertoEjecutorCobro,
        intervalo_dias: int,
        lote: int = LOTE_RECURRENTES,
    ) -> None:
        self._repositorio = repositorio
        self._cobros = cobros
        self._intervalo_dias = intervalo_dias
        self._lote = lote

    def programar(self, propuesta_id: str, poliza_id: str, vigencia_hasta: datetime) -> bool:
        creada = self._repositorio.crear(
            propuesta_id, poliza_id, vigencia_hasta, self._intervalo_dias
        )
        logger.info("programación propuesta=%s creada=%s", id_opaco(propuesta_id), creada)
        return creada

    def generar(self, ahora: datetime | None = None) -> int:
        """Una pasada. Un cobro que falla no frena a los demás: ya está guardado y lo retoma la
        recuperación de atascados."""
        cobros = self._repositorio.reservar_ciclos(ahora or datetime.now(UTC), self._lote)
        for cobro in cobros:
            try:
                self._cobros.ejecutar_cobro(cobro)
            except Exception as error:
                logger.warning(
                    "cobro recurrente sin ejecutar poliza=%s error=%s",
                    id_opaco(cobro["poliza_id"]),
                    type(error).__name__,
                )
        return len(cobros)
