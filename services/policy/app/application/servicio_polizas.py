"""Consultar una póliza emitida (HU-W03, KAN-212, CA-W03-07)."""

from typing import Any, Protocol

from app.application.servicio_emision import numero_de_poliza


class PuertoRepositorioPolizas(Protocol):
    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None: ...


class ServicioPolizas:
    def __init__(self, repositorio: PuertoRepositorioPolizas) -> None:
        self._repositorio = repositorio

    def obtener(self, poliza_id: str) -> dict[str, Any] | None:
        """La póliza con su número formateado, la prima y la cobertura; `None` si no existe."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        return {**poliza, "numero": numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])}
