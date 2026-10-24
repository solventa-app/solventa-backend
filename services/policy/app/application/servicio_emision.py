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

Un cobro que se recupera **después** (el back-office reintenta un rechazado, o un pendiente agotó
las consultas y la pasarela lo cobró más tarde) reabre la propuesta, pero solo por
`reconciliar_cobro`: la llaman el back-office y el barrido de reconciliación, nunca el cliente
(decisión W03-14). Un `PAGO_RECHAZADO` no se emite por la emisión normal.

Si UNDER o PAYMENTS no responden se lanza `EmisionPendiente` y la propuesta no cambia: no se decide
a ciegas ni se da el cobro por hecho. Un primer cobro pendiente se vuelve a consultar con una tarea,
acotado por `max_intentos` para no reprogramarse para siempre."""

import calendar
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_poliza_emitida
from app.application.cliente_acl import PuertoNotificador, SolicitudNotificacion
from app.application.cliente_payments import (
    CobroEnConflicto,
    OrdenPrimerCobro,
    PaymentsNoDisponible,
    PuertoPayments,
)
from app.application.cliente_payments import ResultadoCobro as ResultadoCobroPayments
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
# Lo que la reconciliación puede reabrir: un pago rechazado o un pendiente que dejó de consultarse.
DESDE_RECONCILIACION = ("PAGO_RECHAZADO", "PENDIENTE_PAGO")


class PuertoRepositorioEmision(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def registrar_intento(self, propuesta_id: str) -> int: ...

    def reiniciar_intentos(self, propuesta_id: str) -> None: ...

    def listar_para_reconciliar(
        self, estados: tuple[str, ...], dias: int, limite: int
    ) -> list[str]: ...

    def cambiar_estado(
        self, propuesta_id: str, nuevo: str, desde: tuple[str, ...], motivo: str | None = None
    ) -> bool: ...

    def emitir_poliza(
        self,
        propuesta_id: str,
        cliente_id: str,
        vigencia_desde: Any,
        vigencia_hasta: Any,
        desde: tuple[str, ...] = DESDE_COBRO,
    ) -> tuple[dict[str, Any], bool]: ...

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None: ...

    def marcar_notificada(self, poliza_id: str) -> None: ...


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
        notificador: PuertoNotificador,
        token_medio_pago: str,
        demora_cobro_pendiente_s: float = 10.0,
        max_intentos: int = 12,
        lote_reconciliacion: int = 100,
        dias_reconciliacion: int = 30,
    ) -> None:
        self._repositorio = repositorio
        self._under = under
        self._payments = payments
        self._bus = bus
        self._planificador = planificador
        self._notificador = notificador
        self._token = token_medio_pago
        self._demora_s = demora_cobro_pendiente_s
        self._max_intentos = max_intentos
        self._lote_reconciliacion = lote_reconciliacion
        self._dias_reconciliacion = dias_reconciliacion

    def emitir(self, propuesta_id: str) -> str:
        """Avanza la emisión y devuelve el estado de la propuesta. Idempotente."""
        propuesta = self._repositorio.obtener(propuesta_id)
        if propuesta is None:
            raise PropuestaNoEncontradaParaEmitir(propuesta_id)
        estado = propuesta["estado"]
        if estado == "EMITIDA":
            self._completar_aviso(propuesta)  # si el aviso quedó sin enviar, se reintenta
            return estado
        if estado not in DESDE_COBRO:
            return estado  # en un estado final, o todavía sin firmar

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
        cobro = self._pedir_cobro(propuesta, evidencia, DESDE_COBRO)
        if cobro is None:
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

    def _pedir_cobro(
        self, propuesta: dict[str, Any], evidencia: dict[str, Any], desde: tuple[str, ...]
    ) -> ResultadoCobroPayments | None:
        """Envía la orden del primer cobro, que es siempre la misma (`id_intento_cobro` fijo):
        PAYMENTS crea el cobro la primera vez y las siguientes devuelve el que ya existe, en el
        estado que tenga, **sin cobrar de nuevo**. Por eso sirve también para consultarlo.
        `None` si la orden chocó con un cobro distinto: la propuesta pasa a revisión manual."""
        propuesta_id = propuesta["id"]
        try:
            return self._payments.crear_cobro(
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
                propuesta_id, "EN_REVISION", desde, "COBRO_EN_CONFLICTO"
            )
            return None

    def reconciliar_cobro(self, propuesta_id: str) -> str:
        """Revisa el cobro de una propuesta cuyo pago quedó rechazado o pendiente y, si ya se
        cobró, emite la póliza (CA-W03-06, decisión W03-14). Idempotente.

        Solo la llaman el back-office (tras reintentar un cobro) y el barrido de reconciliación:
        el cliente no la dispara. Devuelve el estado de la propuesta."""
        propuesta = self._repositorio.obtener(propuesta_id)
        if propuesta is None:
            raise PropuestaNoEncontradaParaEmitir(propuesta_id)
        estado = propuesta["estado"]
        if estado == "EMITIDA":
            self._completar_aviso(propuesta)
            return estado
        if estado not in DESDE_RECONCILIACION:
            return estado
        evidencia = self._repositorio.obtener_evidencia(propuesta_id)
        if evidencia is None:
            return estado  # nunca se emite sin la firma

        cobro = self._pedir_cobro(propuesta, evidencia, DESDE_RECONCILIACION)
        if cobro is None:
            return "EN_REVISION"
        if cobro.estado == "cobrado":
            try:
                return self._emitir_poliza(propuesta, DESDE_RECONCILIACION)
            except EmisionPendiente:
                # La póliza ya está emitida y no se deshace; solo falló el aviso. No hay quien
                # llame a esta ruta otra vez, así que se deja una tarea que lo reintente.
                self._planificador.encolar(propuesta_id, self._demora_s)
                return "EMITIDA"
        if cobro.estado == "rechazado":
            self._actualizar_motivo(propuesta, "PAGO_RECHAZADO", cobro.motivo)
            return "PAGO_RECHAZADO"

        # cobrando o pendiente: el cobro se está reintentando.
        self._actualizar_motivo(propuesta, "PENDIENTE_PAGO", cobro.motivo)
        if estado == "PAGO_RECHAZADO":
            # Se reabrió: tiene derecho a otra tanda de consultas, como un primer cobro pendiente.
            self._repositorio.reiniciar_intentos(propuesta_id)
            self._planificador.encolar(propuesta_id, self._demora_s)
        return "PENDIENTE_PAGO"

    def _actualizar_motivo(self, propuesta: dict[str, Any], nuevo: str, motivo: str | None) -> None:
        """Solo toca la propuesta si algo cambió: si no, cada pasada del barrido renovaría su
        `actualizado_en` y la propuesta nunca saldría de la ventana de reconciliación."""
        if propuesta["estado"] != nuevo or propuesta.get("motivo_estado") != motivo:
            self._repositorio.cambiar_estado(propuesta["id"], nuevo, DESDE_RECONCILIACION, motivo)

    def reconciliar_pendientes(self) -> dict[str, int]:
        """Una pasada del proceso de reconciliación: revisa, en lotes y de las más antiguas a las
        más nuevas, las propuestas con el pago rechazado o pendiente de los últimos días. Una que
        falle (PAYMENTS caído) no detiene a las demás; se cuenta y la próxima pasada la retoma."""
        resumen = {"revisadas": 0, "emitidas": 0, "rechazadas": 0, "pendientes": 0, "fallidas": 0}
        ids = self._repositorio.listar_para_reconciliar(
            DESDE_RECONCILIACION, self._dias_reconciliacion, self._lote_reconciliacion
        )
        for propuesta_id in ids:
            try:
                estado = self.reconciliar_cobro(propuesta_id)
            except (EmisionPendiente, PropuestaNoEncontradaParaEmitir):
                resumen["fallidas"] += 1
                continue
            resumen["revisadas"] += 1
            clave = {
                "EMITIDA": "emitidas",
                "PAGO_RECHAZADO": "rechazadas",
                "PENDIENTE_PAGO": "pendientes",
            }.get(estado)
            if clave is not None:
                resumen[clave] += 1
        logger.info("reconciliación de cobros %s", resumen)
        return resumen

    def _emitir_poliza(
        self, propuesta: dict[str, Any], desde: tuple[str, ...] = DESDE_COBRO
    ) -> str:
        ahora = datetime.now(UTC)
        poliza, creada = self._repositorio.emitir_poliza(
            propuesta["id"],
            propuesta["cliente_id"],
            ahora,
            sumar_meses(ahora, int(propuesta["plazo_meses"])),
            desde,
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
        numero = numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])
        self._avisar(poliza, propuesta["id"], numero)
        return "EMITIDA"

    def _completar_aviso(self, propuesta: dict[str, Any]) -> None:
        """Una póliza ya emitida a la que no se le pudo avisar: se reintenta el aviso."""
        poliza = self._repositorio.obtener_poliza(propuesta["poliza_id"])
        if poliza is None or poliza["notificada_en"] is not None:
            return
        numero = numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])
        self._avisar(poliza, propuesta["id"], numero)

    def _avisar(self, poliza: dict[str, Any], propuesta_id: str, numero: str) -> None:
        """Avisa al titular que firmó, una sola vez por póliza (CA-W03-08). Si no se puede, lanza
        `EmisionPendiente`: la póliza ya está emitida y no se deshace; solo se reintenta el
        aviso."""
        if poliza.get("notificada_en") is not None:
            return
        evidencia = self._repositorio.obtener_evidencia(propuesta_id)
        resultado = self._notificador.notificar(
            SolicitudNotificacion(
                id_notificacion=f"poliza-emitida-{poliza['id']}",
                plantilla="poliza-emitida",
                destinatario_id=evidencia["firmante_id"],
                datos={
                    "numero_poliza": numero,
                    "enlace_documento": f"/polizas/{poliza['id']}/documento",
                },
            )
        )
        if resultado.estado != "enviada":
            raise EmisionPendiente("notificaciones no disponibles")
        self._repositorio.marcar_notificada(poliza["id"])
