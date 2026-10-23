"""Emitir la póliza: decisión de UNDER, primer cobro con PAYMENTS y emisión (HU-W03, KAN-212 y
KAN-213, CA-W03-04 a CA-W03-07 y CA-W03-09; decisión D-03: se cobra antes de emitir).

    EN_EMISION ──UNDER──► RECHAZADA        ─► NO_EMITIDA
                       ├─ REVISION_MANUAL  ─► EN_REVISION
                       └─ APROBADA ──cobro──► cobrado    ─► EMITIDA (póliza + `poliza.emitida`)
                                          ├─ rechazado  ─► PAGO_RECHAZADO
                                          └─ pendiente  ─► PENDIENTE_PAGO (se vuelve a consultar)

UNDER decide antes del cobro para no cobrarle a quien luego se rechaza. Si el cobro queda pendiente
la póliza **espera**: nunca se emite sin cobrar (CA-W03-06). Un rechazo del cobro tampoco emite.

Todo es idempotente: la orden de cobro tiene un `id_intento_cobro` fijo (derivado de la propuesta) y
PAYMENTS devuelve el cobro que ya existe sin cobrar de nuevo; la póliza se crea una vez por
propuesta. Por eso Cloud Tasks puede repetir la tarea, o dos tareas correr a la vez, sin
duplicar nada.

Si UNDER o PAYMENTS no responden se lanza `EmisionPendiente` y la propuesta no cambia: no se decide
a ciegas ni se da el cobro por hecho. Un primer cobro pendiente se vuelve a consultar con una tarea,
acotado por `max_intentos` para no reprogramarse para siempre."""

import calendar
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_poliza_emitida
from app.application.cliente_payments import (
    CobroEnConflicto,
    OrdenPrimerCobro,
    PaymentsNoDisponible,
    PuertoPayments,
)
from app.application.cliente_under import PuertoUnder, UnderNoDisponible
from app.application.identificadores import id_opaco
from app.application.planificador import EmisionPendiente, PuertoPlanificador

logger = logging.getLogger("policy.emision")

# Derivar el `id_intento_cobro` de la propuesta lo hace determinista: reintentar no cobra dos veces.
ESPACIO_INTENTOS = uuid.UUID("5c0f6c1e-7d3b-4a55-9a58-3f6f2f6d0a11")

DECISION_APROBADA = "APROBADA"
DECISION_RECHAZADA = "RECHAZADA"

DESDE_EMISION = ("EN_EMISION",)
DESDE_COBRO = ("EN_EMISION", "PENDIENTE_PAGO")


class PuertoRepositorioEmision(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def registrar_intento(self, propuesta_id: str) -> int: ...

    def cambiar_estado(
        self, propuesta_id: str, nuevo: str, desde: tuple[str, ...], motivo: str | None = None
    ) -> bool: ...

    def emitir_poliza(
        self, propuesta_id: str, cliente_id: str, vigencia_desde: Any, vigencia_hasta: Any
    ) -> tuple[dict[str, Any], bool]: ...


class PropuestaNoEncontradaParaEmitir(Exception):
    pass


def id_intento_cobro(propuesta_id: str) -> str:
    return str(uuid.uuid5(ESPACIO_INTENTOS, propuesta_id))


def sumar_meses(fecha: datetime, meses: int) -> datetime:
    mes = fecha.month - 1 + meses
    anio = fecha.year + mes // 12
    mes = mes % 12 + 1
    return fecha.replace(
        year=anio, month=mes, day=min(fecha.day, calendar.monthrange(anio, mes)[1])
    )


def numero_de_poliza(numero: int, fecha: datetime) -> str:
    return f"SOL-{fecha.year}-{numero:06d}"


class ServicioEmision:
    def __init__(
        self,
        repositorio: PuertoRepositorioEmision,
        under: PuertoUnder,
        payments: PuertoPayments,
        bus: PuertoBusEventos,
        planificador: PuertoPlanificador,
        token_medio_pago: str,
        demora_cobro_pendiente_s: float = 10.0,
        max_intentos: int = 12,
    ) -> None:
        self._repositorio = repositorio
        self._under = under
        self._payments = payments
        self._bus = bus
        self._planificador = planificador
        self._token = token_medio_pago
        self._demora_s = demora_cobro_pendiente_s
        self._max_intentos = max_intentos

    def emitir(self, propuesta_id: str) -> str:
        """Avanza la emisión y devuelve el estado de la propuesta. Idempotente."""
        propuesta = self._repositorio.obtener(propuesta_id)
        if propuesta is None:
            raise PropuestaNoEncontradaParaEmitir(propuesta_id)
        estado = propuesta["estado"]
        if estado not in DESDE_COBRO:
            return estado  # ya emitida, en un estado final, o todavía sin firmar

        evidencia = self._repositorio.obtener_evidencia(propuesta_id)
        if evidencia is None:
            return estado  # nunca se emite sin la firma

        intentos = self._repositorio.registrar_intento(propuesta_id)
        if intentos > self._max_intentos:
            logger.warning(
                "emisión sin cerrar tras %s intentos propuesta=%s",
                intentos - 1,
                id_opaco(propuesta_id),
            )
            return estado

        if estado == "EN_EMISION":
            cerrada = self._decidir(propuesta)
            if cerrada is not None:
                return cerrada
        return self._cobrar_y_emitir(propuesta, evidencia, intentos)

    def _decidir(self, propuesta: dict[str, Any]) -> str | None:
        """UNDER decide. `None` si se aprobó y sigue el cobro; el estado final si no."""
        try:
            resultado = self._under.decidir(
                propuesta["id"],
                propuesta["cliente_id"],
                propuesta.get("nivel_riesgo"),
                propuesta["cobertura"],
            )
        except UnderNoDisponible as error:
            raise EmisionPendiente("UNDER no disponible") from error
        if resultado.decision == DECISION_APROBADA:
            return None
        nuevo = "NO_EMITIDA" if resultado.decision == DECISION_RECHAZADA else "EN_REVISION"
        self._repositorio.cambiar_estado(
            propuesta["id"], nuevo, DESDE_EMISION, ",".join(resultado.motivos) or None
        )
        logger.info(
            "propuesta %s -> %s motivos=%s",
            id_opaco(propuesta["id"]),
            nuevo,
            ",".join(resultado.motivos) or "-",
        )
        return nuevo

    def _cobrar_y_emitir(
        self, propuesta: dict[str, Any], evidencia: dict[str, Any], intentos: int
    ) -> str:
        propuesta_id = propuesta["id"]
        try:
            cobro = self._payments.crear_cobro(
                OrdenPrimerCobro(
                    id_intento_cobro=id_intento_cobro(propuesta_id),
                    poliza_id=propuesta_id,
                    cliente_id=evidencia["firmante_id"],
                    monto=propuesta["prima"]["valor"],
                    moneda=propuesta["prima"]["moneda"],
                    token_medio_pago=self._token,
                )
            )
        except PaymentsNoDisponible as error:
            raise EmisionPendiente("PAYMENTS no disponible") from error
        except CobroEnConflicto:
            # No debería pasar con una orden fija. No se emite ni se rechaza: revisión manual.
            self._repositorio.cambiar_estado(
                propuesta_id, "EN_REVISION", DESDE_COBRO, "COBRO_EN_CONFLICTO"
            )
            return "EN_REVISION"

        if cobro.estado == "cobrado":
            return self._emitir_poliza(propuesta)
        if cobro.estado == "rechazado":
            self._repositorio.cambiar_estado(
                propuesta_id, "PAGO_RECHAZADO", DESDE_COBRO, cobro.motivo
            )
            return "PAGO_RECHAZADO"

        # cobrando o pendiente: la póliza espera y se vuelve a consultar con una tarea.
        self._repositorio.cambiar_estado(propuesta_id, "PENDIENTE_PAGO", DESDE_COBRO, cobro.motivo)
        if intentos < self._max_intentos:
            self._planificador.encolar(propuesta_id, self._demora_s)
        return "PENDIENTE_PAGO"

    def _emitir_poliza(self, propuesta: dict[str, Any]) -> str:
        ahora = datetime.now(UTC)
        poliza, creada = self._repositorio.emitir_poliza(
            propuesta["id"],
            propuesta["cliente_id"],
            ahora,
            sumar_meses(ahora, int(propuesta["plazo_meses"])),
        )
        if poliza is None:
            # Otra emisión movió la propuesta a un estado final mientras tanto: no se pisa.
            return self._repositorio.obtener(propuesta["id"])["estado"]
        if creada:
            self._bus.publicar(
                construir_evento_poliza_emitida(
                    poliza["id"],
                    numero_de_poliza(poliza["numero"], poliza["vigencia_desde"]),
                    propuesta["id"],
                    propuesta["cliente_id"],
                    poliza["vigencia_desde"],
                    poliza["vigencia_hasta"],
                )
            )
            logger.info("póliza emitida propuesta=%s", id_opaco(propuesta["id"]))
        return "EMITIDA"
