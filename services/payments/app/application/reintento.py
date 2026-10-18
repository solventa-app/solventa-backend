"""Reintento de cobros pendientes — PLACEHOLDER local de Cloud Tasks (T-W05-4).

No hay Cloud Tasks real disponible (sin proyecto GCP, ver `CLAUDE.md`). Esto
es un bucle `asyncio` periódico arrancado desde el lifespan de FastAPI (mismo
patrón que `SondaRecuperacion` en `services/acl-worker/app/application/sonda.py`):
cada `intervalo_s` busca cobros en estado `pendiente` con menos de
`max_intentos` intentos y vuelve a llamar a `POST {ACL_URL}/pagos/cobrar` con
el MISMO `id_intento_cobro` (CA-W05-05: el reintento reutiliza la clave),
**conciliando antes** con la pasarela (`app/application/conciliacion.py`, KAN-28).

**Acotado, no reintenta para siempre:** `listar_pendientes` (en
`repositorio_cobros.py`) solo devuelve cobros con `intentos < max_intentos`.
Una vez que un cobro agota los intentos sin resolverse, este bucle deja de
tocarlo: queda `pendiente` para el back-office, que lo ve en `GET /cobros` y lo
reintenta a mano con `POST /cobros/{id}/reintentar` (KAN-28). Los intentos que no
llegaron a la pasarela (circuito abierto o conciliación sin respuesta) no cuentan.

**Aislamiento de la decisión GCP (regla de arquitectura #3, sin puertos y
adaptadores para esto):** `ejecutar_una_pasada` es la lógica de reintento en
sí — no sabe ni le importa qué la dispara. `ReintentoCobrosPendientes` es
HOY lo que la dispara (un bucle `asyncio.sleep` en este proceso). Cuando
`infra-engineer` tenga un proyecto GCP real, lo que cambia es ESO: una tarea
de Cloud Tasks que llame a un endpoint interno (p. ej. `POST /cobros/_reintentar`)
que a su vez invoque `ejecutar_una_pasada` — la lógica de PAYMENTS no se
toca, solo el disparador."""

import asyncio
import logging

from app.application.bus_eventos import PuertoBusEventos
from app.application.cliente_acl import PuertoClienteAcl
from app.application.conciliacion import procesar_reintento
from app.application.disparador import PuertoPlanificador
from app.application.servicio_cobros import RepositorioCobros

logger = logging.getLogger("payments.reintento")


MINUTOS_COBRO_ATASCADO = 2.0


def ejecutar_una_pasada(
    repositorio: RepositorioCobros,
    cliente_acl: PuertoClienteAcl,
    bus: PuertoBusEventos,
    max_intentos: int,
    minutos_atascado: float = MINUTOS_COBRO_ATASCADO,
) -> int:
    """Una pasada del reintento: función pequeña y aislada (ver docstring del
    módulo) reusada tal cual por el bucle en background y por las pruebas
    (sin `asyncio`, sin esperar ningún `intervalo_s`). Cada cobro pasa por
    `procesar_reintento` (concilia antes de cobrar, KAN-28). Devuelve cuántos
    cobros se procesaron en esta pasada.

    Además de los `pendiente`, recupera los **atascados**: un cobro que sigue en `cobrando` pasados
    `minutos_atascado` porque el proceso se cayó a mitad del cobro. Se trata igual: se concilia
    con la pasarela antes de volver a cobrar, así que no hay doble cobro.

    Un cobro que lanza un error no frena a los demás: se registra y se vuelve a intentar en la
    próxima pasada."""
    por_procesar = repositorio.listar_pendientes(max_intentos) + repositorio.listar_atascados(
        minutos_atascado
    )
    for cobro in por_procesar:
        try:
            procesar_reintento(cobro, repositorio, cliente_acl, bus)
        except Exception:
            logger.exception("reintento: falló un cobro; se sigue con los demás")
    return len(por_procesar)


class ReintentoCobrosPendientes:
    def __init__(
        self,
        repositorio: RepositorioCobros,
        cliente_acl: PuertoClienteAcl,
        bus: PuertoBusEventos,
        intervalo_segundos: float,
        max_intentos: int,
        planificador: PuertoPlanificador | None = None,
        minutos_atascado: float = MINUTOS_COBRO_ATASCADO,
    ) -> None:
        self._repositorio = repositorio
        self._cliente_acl = cliente_acl
        self._bus = bus
        self._intervalo_segundos = intervalo_segundos
        self._max_intentos = max_intentos
        self._planificador = planificador
        self._minutos_atascado = minutos_atascado
        self._tarea: asyncio.Task | None = None

    def una_pasada_externa(self) -> dict[str, int]:
        """Una pasada disparada desde fuera (Cloud Tasks, `DISPARADOR=externo`). Si después de
        procesar todavía quedan cobros por reintentar, programa la siguiente: la cadena sigue
        sola hasta que no quede ninguno (o se agoten sus intentos)."""
        procesados = ejecutar_una_pasada(
            self._repositorio,
            self._cliente_acl,
            self._bus,
            self._max_intentos,
            self._minutos_atascado,
        )
        quedan = len(self._repositorio.listar_pendientes(self._max_intentos)) + len(
            self._repositorio.listar_atascados(self._minutos_atascado)
        )
        if quedan > 0 and self._planificador is not None:
            self._planificador.programar(self._intervalo_segundos)
        return {"procesados": procesados, "por_reintentar": quedan}

    def iniciar(self) -> None:
        self._tarea = asyncio.create_task(self._bucle())

    async def detener(self) -> None:
        """Cancela el bucle de reintento de cobros y espera a que termine.

        Su cancelación es lo esperado, así que `gather` la devuelve como resultado;
        si se cancela este apagado, la propaga."""
        if self._tarea is None:
            return
        self._tarea.cancel()
        await asyncio.gather(self._tarea, return_exceptions=True)

    async def _bucle(self) -> None:
        while True:
            await asyncio.sleep(self._intervalo_segundos)
            try:
                await asyncio.to_thread(
                    ejecutar_una_pasada,
                    self._repositorio,
                    self._cliente_acl,
                    self._bus,
                    self._max_intentos,
                    self._minutos_atascado,
                )
            except Exception:
                # P. ej. la base reiniciándose: un error no mata el bucle; se registra y sigue en el
                # próximo ciclo.
                logger.exception("reintento: falló la pasada; se reintenta en el próximo ciclo")
