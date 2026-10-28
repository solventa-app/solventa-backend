"""Consultar una póliza emitida y generar su documento (HU-W03, KAN-212 y KAN-215).

Bloque 9: el PDF estampa la firma del titular. La imagen (trazo o cargada) se baja de ALMACENAMIENTO
con una URL de minutos al generar el documento y no se guarda; el nombre estilizado sale de la
propuesta. W03-9 sigue: el PDF no se almacena."""

import logging
from typing import Any, Protocol

from app.application.cliente_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoRechazado,
    PuertoAlmacenamiento,
)
from app.application.documento_poliza import generar_borrador, generar_pdf
from app.application.servicio_emision import numero_de_poliza

logger = logging.getLogger("policy.polizas")


class DocumentoNoDisponible(Exception):
    """No se pudo bajar la imagen de la firma: el documento no sale sin ella (se reintenta)."""


class PuertoRepositorioPolizas(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None: ...

    def contacto(self, propuesta_id: str) -> dict[str, str | None] | None: ...


class ServicioPolizas:
    def __init__(
        self,
        repositorio: PuertoRepositorioPolizas,
        ambiente_pruebas: bool = True,
        almacenamiento: PuertoAlmacenamiento | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._ambiente_pruebas = ambiente_pruebas
        self._almacenamiento = almacenamiento

    def obtener(self, poliza_id: str) -> dict[str, Any] | None:
        """La póliza con su número formateado, la prima y la cobertura; `None` si no existe."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        return {**poliza, "numero": numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])}

    def documento(self, poliza_id: str, asegurado: str | None = None) -> bytes | None:
        """El PDF de la póliza, generado al pedirlo; `None` si la póliza no existe. El nombre del
        asegurado llega de quien lo pide y no se guarda. Lanza `DocumentoNoDisponible` si la imagen
        de la firma no se pudo bajar."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        propuesta = self._repositorio.obtener(poliza["propuesta_id"])
        evidencia = self._repositorio.obtener_evidencia(poliza["propuesta_id"])
        numero = numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])
        imagen, texto = self._firma(poliza["propuesta_id"], evidencia)
        return generar_pdf(
            poliza,
            propuesta,
            evidencia,
            numero,
            asegurado,
            ambiente_pruebas=self._ambiente_pruebas,
            firma_imagen=imagen,
            firma_texto=texto,
        )

    def borrador(self, propuesta: dict[str, Any]) -> bytes:
        """El contrato a firmar, en PDF (W03-31)."""
        return generar_borrador(propuesta, ambiente_pruebas=self._ambiente_pruebas)

    def _firma(self, propuesta_id: str, evidencia: dict | None) -> tuple[bytes | None, str | None]:
        if not evidencia or not evidencia.get("modo_firma"):
            return None, None  # flujo anterior al bloque 9: solo el sello
        if evidencia["modo_firma"] == "NOMBRE":
            return None, (self._repositorio.contacto(propuesta_id) or {}).get("firma_texto")
        archivo_id = evidencia.get("imagen_firma_archivo_id")
        if not archivo_id or self._almacenamiento is None:
            return None, None
        try:
            return self._almacenamiento.bajar(archivo_id), None
        except ArchivoRechazado:
            # Ya no está (p. ej. borrada por la purga de una propuesta cancelada): sin imagen.
            return None, None
        except AlmacenamientoNoDisponible as error:
            raise DocumentoNoDisponible("almacenamiento no disponible") from error
