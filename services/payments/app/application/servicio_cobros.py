"""Caso de uso: cobrar con idempotencia real y balances por póliza (T-W05-3).

Depende de `RepositorioCobros` y `PuertoClienteAcl` (`Protocol` estructural,
no puertos hexagonales formales — regla de arquitectura #3) para poder
probarse con dobles sin levantar PostgreSQL ni el ACL Worker reales."""

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_cobro_estado_cambiado
from app.application.cliente_acl import OrdenCobro, PuertoClienteAcl
from app.application.conciliacion import procesar_reintento, referencia_ante_pasarela
from app.application.identificadores import id_opaco

logger = logging.getLogger("payments.cobros")

# Reintentos manuales de un `rechazado` por defecto (el número del prototipo W05-01).
REINTENTOS_RECHAZO_MAXIMO = 3


def mensaje_reintentos_agotados(maximo: int) -> str:
    return f"Se agotaron los {maximo} reintentos: el cobro requiere otro medio de pago."


class NoReintentable(Exception):
    """El cobro no se puede reintentar (hoy: rechazado y sin reintentos). Mensaje apto para
    el usuario."""


MENSAJE_CONFLICTO = (
    "Ese id_intento_cobro ya se usó con otros datos: genera uno nuevo para una orden distinta."
)


class IntentoCobroEnConflicto(Exception):
    """El `id_intento_cobro` ya existe con otra póliza, monto, moneda o medio de pago."""


def _mismo_monto(a: str, b: str) -> bool:
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return a == b


def _misma_orden(cobro: dict[str, Any], orden: dict[str, Any]) -> bool:
    return (
        cobro["poliza_id"] == orden["poliza_id"]
        and cobro.get("cliente_id") == orden.get("cliente_id")
        and cobro["moneda"] == orden["moneda"]
        and cobro["token_medio_pago"] == orden["token_medio_pago"]
        and _mismo_monto(cobro["monto"], orden["monto"])
    )


class RepositorioCobros(Protocol):
    def crear_o_obtener(self, cobro_id: str, orden: dict[str, Any]) -> tuple[dict, bool]: ...

    def actualizar_estado(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict: ...

    def actualizar_estado_y_contar_intento(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict: ...

    def iniciar_reintento_rechazo(self, cobro_id: str, maximo: int) -> dict | None: ...

    def listar_pendientes(self, max_intentos: int) -> list[dict]: ...

    def obtener(self, cobro_id: str) -> dict | None: ...

    def listar(self, poliza_id: str | None, estado: str | None) -> list[dict]: ...

    def balance_poliza(self, poliza_id: str) -> str: ...


class ServicioCobros:
    def __init__(
        self,
        repositorio: RepositorioCobros,
        cliente_acl: PuertoClienteAcl,
        bus: PuertoBusEventos,
        reintentos_rechazo_maximo: int = REINTENTOS_RECHAZO_MAXIMO,
    ) -> None:
        self._repositorio = repositorio
        self._cliente_acl = cliente_acl
        self._bus = bus
        self.reintentos_rechazo_maximo = reintentos_rechazo_maximo

    def crear_cobro(self, orden: dict[str, Any]) -> dict[str, Any]:
        """Idempotencia (CA-W05-04): si `id_intento_cobro` ya existía, NO se
        vuelve a llamar al ACL Worker — se devuelve la fila ya persistida tal
        cual (sea cual sea su estado actual). Si llega con otros datos, es un error
        del cliente y se rechaza (`IntentoCobroEnConflicto`), nunca se ignora."""
        cobro_id = str(uuid.uuid4())
        cobro, creado = self._repositorio.crear_o_obtener(cobro_id, orden)

        if not creado:
            if not _misma_orden(cobro, orden):
                logger.info(
                    "id_intento_cobro repetido con otros datos poliza=%s",
                    id_opaco(cobro["poliza_id"]),
                )
                raise IntentoCobroEnConflicto(MENSAJE_CONFLICTO)
            logger.info(
                "cobro ya existia para este id_intento_cobro poliza=%s estado=%s",
                id_opaco(cobro["poliza_id"]),
                cobro["estado"],
            )
            return cobro

        logger.info("cobro creado poliza=%s estado=cobrando", id_opaco(cobro["poliza_id"]))
        self._emitir(cobro)

        resultado = self._cliente_acl.cobrar(
            OrdenCobro(
                id_intento_cobro=cobro["id_intento_cobro"],
                poliza_id=cobro["poliza_id"],
                monto=cobro["monto"],
                moneda=cobro["moneda"],
                token_medio_pago=cobro["token_medio_pago"],
            )
        )

        cobro_actualizado = self._repositorio.actualizar_estado(
            cobro["id"], resultado.estado, resultado.referencia, resultado.motivo
        )
        logger.info(
            "cobro actualizado poliza=%s estado=%s",
            id_opaco(cobro_actualizado["poliza_id"]),
            cobro_actualizado["estado"],
        )
        self._emitir(cobro_actualizado)
        return cobro_actualizado

    def listar(self, poliza_id: str | None, estado: str | None) -> list[dict[str, Any]]:
        return self._repositorio.listar(poliza_id, estado)

    def obtener(self, cobro_id: str) -> dict[str, Any] | None:
        return self._repositorio.obtener(cobro_id)

    def reintentar(self, cobro_id: str) -> dict[str, Any] | None:
        """Reintento a demanda del back-office (CA-W05-02). `None` si no existe.

        - `pendiente`: concilia y, si hace falta, cobra con la MISMA clave
          (`procesar_reintento`, la misma función del bucle automático).
        - `rechazado`: un intento nuevo ante la pasarela, con su propia referencia
          (`{id_intento_cobro}-rN`), hasta `reintentos_rechazo_maximo` veces (decisión del
          equipo, prototipo W05-01). Agotados: `NoReintentable`.
        - `cobrado` o `cobrando`: se devuelve tal cual (un doble clic no cobra dos veces).
        Funciona aunque el cobro ya haya agotado los intentos del bucle automático."""
        cobro = self._repositorio.obtener(cobro_id)
        if cobro is None:
            return None
        if cobro["estado"] == "rechazado":
            return self._reintentar_rechazado(cobro)
        if cobro["estado"] != "pendiente":
            return cobro
        logger.info("reintento manual poliza=%s", id_opaco(cobro["poliza_id"]))
        return procesar_reintento(cobro, self._repositorio, self._cliente_acl, self._bus)

    def _reintentar_rechazado(self, cobro: dict[str, Any]) -> dict[str, Any]:
        maximo = self.reintentos_rechazo_maximo
        if cobro.get("reintentos_rechazo", 0) >= maximo:
            raise NoReintentable(mensaje_reintentos_agotados(maximo))
        iniciado = self._repositorio.iniciar_reintento_rechazo(cobro["id"], maximo)
        if iniciado is None:
            # Otro clic ganó (o se agotó justo ahora): se devuelve como quedó, sin cobrar.
            actual = self._repositorio.obtener(cobro["id"])
            if actual is not None and actual["estado"] == "rechazado":
                raise NoReintentable(mensaje_reintentos_agotados(maximo))
            return actual or cobro
        logger.info(
            "reintento de un rechazado poliza=%s reintento=%s",
            id_opaco(iniciado["poliza_id"]),
            iniciado["reintentos_rechazo"],
        )
        self._emitir(iniciado)
        resultado = self._cliente_acl.cobrar(
            OrdenCobro(
                id_intento_cobro=referencia_ante_pasarela(iniciado),
                poliza_id=iniciado["poliza_id"],
                monto=iniciado["monto"],
                moneda=iniciado["moneda"],
                token_medio_pago=iniciado["token_medio_pago"],
            )
        )
        actualizado = self._repositorio.actualizar_estado(
            iniciado["id"], resultado.estado, resultado.referencia, resultado.motivo
        )
        self._emitir(actualizado)
        return actualizado

    def reintentos_restantes(self, cobro: dict[str, Any]) -> int:
        return max(0, self.reintentos_rechazo_maximo - cobro.get("reintentos_rechazo", 0))

    def balance_poliza(self, poliza_id: str) -> str:
        return self._repositorio.balance_poliza(poliza_id)

    def _emitir(self, cobro: dict[str, Any]) -> None:
        evento = construir_evento_cobro_estado_cambiado(
            cobro_id=cobro["id"],
            poliza_id=cobro["poliza_id"],
            estado=cobro["estado"],
            id_intento_cobro=cobro["id_intento_cobro"],
        )
        self._bus.publicar(evento)
