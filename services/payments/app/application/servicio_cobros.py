"""Caso de uso: cobrar con idempotencia real y balances por póliza (T-W05-3).

Depende de `RepositorioCobros` y `PuertoClienteAcl` (`Protocol` estructural,
no puertos hexagonales formales — regla de arquitectura #3) para poder
probarse con dobles sin levantar PostgreSQL ni el ACL Worker reales."""

import logging
import uuid
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_cobro_estado_cambiado
from app.application.cliente_acl import OrdenCobro, PuertoClienteAcl
from app.application.identificadores import id_opaco

logger = logging.getLogger("payments.cobros")


class RepositorioCobros(Protocol):
    def crear_o_obtener(self, cobro_id: str, orden: dict[str, Any]) -> tuple[dict, bool]: ...

    def actualizar_estado(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict: ...

    def actualizar_estado_y_contar_intento(
        self, cobro_id: str, estado: str, referencia: str | None, motivo: str | None
    ) -> dict: ...

    def listar_pendientes(self, max_intentos: int) -> list[dict]: ...

    def balance_poliza(self, poliza_id: str) -> str: ...


class ServicioCobros:
    def __init__(
        self,
        repositorio: RepositorioCobros,
        cliente_acl: PuertoClienteAcl,
        bus: PuertoBusEventos,
    ) -> None:
        self._repositorio = repositorio
        self._cliente_acl = cliente_acl
        self._bus = bus

    def crear_cobro(self, orden: dict[str, Any]) -> dict[str, Any]:
        """Idempotencia (CA-W05-04): si `clave_idempotencia` ya existía, NO se
        vuelve a llamar al ACL Worker — se devuelve la fila ya persistida tal
        cual (sea cual sea su estado actual)."""
        cobro_id = str(uuid.uuid4())
        cobro, creado = self._repositorio.crear_o_obtener(cobro_id, orden)

        if not creado:
            logger.info(
                "cobro ya existia para esta clave de idempotencia poliza=%s estado=%s",
                id_opaco(cobro["poliza_id"]),
                cobro["estado"],
            )
            return cobro

        logger.info("cobro creado poliza=%s estado=cobrando", id_opaco(cobro["poliza_id"]))
        self._emitir(cobro)

        resultado = self._cliente_acl.cobrar(
            OrdenCobro(
                clave_idempotencia=cobro["clave_idempotencia"],
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

    def balance_poliza(self, poliza_id: str) -> str:
        return self._repositorio.balance_poliza(poliza_id)

    def _emitir(self, cobro: dict[str, Any]) -> None:
        evento = construir_evento_cobro_estado_cambiado(
            cobro_id=cobro["id"],
            poliza_id=cobro["poliza_id"],
            estado=cobro["estado"],
            clave_idempotencia=cobro["clave_idempotencia"],
        )
        self._bus.publicar(evento)
