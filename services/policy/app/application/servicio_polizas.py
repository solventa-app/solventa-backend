"""Consultar una póliza emitida y generar su documento (HU-W03, KAN-212 y KAN-215)."""

from typing import Any, Protocol

from app.application.documento_poliza import generar_pdf
from app.application.servicio_emision import numero_de_poliza


class PuertoRepositorioPolizas(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None: ...


class ServicioPolizas:
    def __init__(
        self, repositorio: PuertoRepositorioPolizas, ambiente_pruebas: bool = True
    ) -> None:
        self._repositorio = repositorio
        self._ambiente_pruebas = ambiente_pruebas

    def obtener(self, poliza_id: str) -> dict[str, Any] | None:
        """La póliza con su número formateado, la prima y la cobertura; `None` si no existe."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        return {**poliza, "numero": numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])}

    def documento(self, poliza_id: str, asegurado: str | None = None) -> bytes | None:
        """El PDF de la póliza, generado al pedirlo; `None` si la póliza no existe. El nombre del
        asegurado llega de quien lo pide y no se guarda."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        propuesta = self._repositorio.obtener(poliza["propuesta_id"])
        evidencia = self._repositorio.obtener_evidencia(poliza["propuesta_id"])
        numero = numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])
        return generar_pdf(
            poliza, propuesta, evidencia, numero, asegurado, ambiente_pruebas=self._ambiente_pruebas
        )
