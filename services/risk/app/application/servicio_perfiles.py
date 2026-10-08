"""Caso de uso: registrar un perfil de riesgo (T-W01-6/7, HU-W01).

RISK recibe `cliente_id` y los datos ya resueltos de las fuentes (el shape
que ya devuelve `acl-worker` en `POST /fuentes/consultar`: `fuente`, `datos`,
`capturado_en`, `de_cache`, `degradado` — no se inventa un contrato nuevo con
el ACL Worker, se reusa ese shape tal cual llega en el cuerpo de la
petición), persiste el perfil versionado y devuelve el `operationTime` de la
escritura (D-02) junto con la versión asignada. Emite `perfil.actualizado`
por el bus de eventos (placeholder, ver `bus_eventos.py`).

Depende de `RepositorioPerfiles` (`Protocol` estructural, no un puerto
hexagonal formal) para poder probarse con un doble sin levantar MongoDB."""

import logging
from typing import Any, Protocol

from app.application.bus_eventos import PuertoBusEventos, construir_evento_perfil_actualizado
from app.application.identificadores import id_opaco
from app.application.tiempo_causal import ComponentesTiempo, serializar_operation_time

logger = logging.getLogger("risk.perfiles")


class RepositorioPerfiles(Protocol):
    def guardar(
        self, cliente_id: str, fuentes: list[dict[str, Any]]
    ) -> tuple[int, ComponentesTiempo]: ...


class ServicioPerfiles:
    def __init__(self, repositorio: RepositorioPerfiles, bus: PuertoBusEventos) -> None:
        self._repositorio = repositorio
        self._bus = bus

    def registrar(self, cliente_id: str, fuentes: list[dict[str, Any]]) -> dict[str, Any]:
        perfil_version, operation_time = self._repositorio.guardar(cliente_id, fuentes)
        operation_time_str = serializar_operation_time(operation_time)

        logger.info(
            "perfil registrado cliente=%s perfil_version=%s",
            id_opaco(cliente_id),
            perfil_version,
        )

        evento = construir_evento_perfil_actualizado(
            cliente_id, perfil_version, operation_time_str
        )
        self._bus.publicar(evento)

        return {
            "cliente_id": cliente_id,
            "perfil_version": perfil_version,
            "operation_time": operation_time_str,
        }
