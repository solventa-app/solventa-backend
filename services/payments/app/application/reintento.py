"""Reintento de cobros pendientes — PLACEHOLDER local de Cloud Tasks (T-W05-4).

No hay Cloud Tasks real disponible (sin proyecto GCP, ver `CLAUDE.md`). Esto
es un bucle `asyncio` periódico arrancado desde el lifespan de FastAPI (mismo
patrón que `SondaRecuperacion` en `services/acl-worker/app/application/sonda.py`):
cada `intervalo_s` busca cobros en estado `pendiente` con menos de
`max_intentos` intentos y vuelve a llamar a `POST {ACL_URL}/pagos/cobrar` con
la MISMA `clave_idempotencia` (CA-W05-05: el reintento reutiliza la clave).

**Acotado, no reintenta para siempre:** `listar_pendientes` (en
`repositorio_cobros.py`) solo devuelve cobros con `intentos < max_intentos`.
Una vez que un cobro agota los intentos sin resolverse, este bucle deja de
tocarlo: queda `pendiente` para seguimiento manual, consultable vía
`GET /polizas/{poliza_id}/balance` o una consulta directa a la tabla `cobros`
(no se construye una DLQ formal aparte — ya es una fila consultable).

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

from app.application.bus_eventos import PuertoBusEventos, construir_evento_cobro_estado_cambiado
from app.application.cliente_acl import OrdenCobro, PuertoClienteAcl
from app.application.identificadores import id_opaco
from app.application.servicio_cobros import RepositorioCobros

logger = logging.getLogger("payments.reintento")


def ejecutar_una_pasada(
    repositorio: RepositorioCobros,
    cliente_acl: PuertoClienteAcl,
    bus: PuertoBusEventos,
    max_intentos: int,
) -> int:
    """Una pasada del reintento: función pequeña y aislada (ver docstring del
    módulo) reusada tal cual por el bucle en background y por las pruebas
    (sin `asyncio`, sin esperar ningún `intervalo_s`). Devuelve cuántos
    cobros pendientes se procesaron en esta pasada."""
    pendientes = repositorio.listar_pendientes(max_intentos)
    for cobro in pendientes:
        resultado = cliente_acl.cobrar(
            OrdenCobro(
                clave_idempotencia=cobro["clave_idempotencia"],
                poliza_id=cobro["poliza_id"],
                monto=cobro["monto"],
                moneda=cobro["moneda"],
                token_medio_pago=cobro["token_medio_pago"],
            )
        )
        actualizado = repositorio.actualizar_estado_y_contar_intento(
            cobro["id"], resultado.estado, resultado.referencia, resultado.motivo
        )
        logger.info(
            "reintento cobro poliza=%s intentos=%s estado=%s",
            id_opaco(actualizado["poliza_id"]),
            actualizado["intentos"],
            actualizado["estado"],
        )
        bus.publicar(
            construir_evento_cobro_estado_cambiado(
                cobro_id=actualizado["id"],
                poliza_id=actualizado["poliza_id"],
                estado=actualizado["estado"],
                clave_idempotencia=actualizado["clave_idempotencia"],
            )
        )
    return len(pendientes)


class ReintentoCobrosPendientes:
    def __init__(
        self,
        repositorio: RepositorioCobros,
        cliente_acl: PuertoClienteAcl,
        bus: PuertoBusEventos,
        intervalo_segundos: float,
        max_intentos: int,
    ) -> None:
        self._repositorio = repositorio
        self._cliente_acl = cliente_acl
        self._bus = bus
        self._intervalo_segundos = intervalo_segundos
        self._max_intentos = max_intentos
        self._tarea: asyncio.Task | None = None

    def iniciar(self) -> None:
        self._tarea = asyncio.create_task(self._bucle())

    async def detener(self) -> None:
        if self._tarea is None:
            return
        self._tarea.cancel()
        try:
            await self._tarea
        except asyncio.CancelledError:
            pass

    async def _bucle(self) -> None:
        while True:
            await asyncio.sleep(self._intervalo_segundos)
            await asyncio.to_thread(
                ejecutar_una_pasada,
                self._repositorio,
                self._cliente_acl,
                self._bus,
                self._max_intentos,
            )
