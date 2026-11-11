"""Consultar una póliza emitida y generar su documento (HU-W03, KAN-212 y KAN-215).

Bloque 9: el PDF estampa la firma del titular. La imagen (trazo o cargada) se baja de ALMACENAMIENTO
con una URL de minutos al generar el documento; el nombre estilizado sale de la propuesta.

KAN-26 (T6, D-1) reemplaza a W03-9: el contrato firmado y la póliza se **guardan cifrados** en
ALMACENAMIENTO (`ServicioDocumentos`) y se sirven desde ahí con la autorización de siempre. El
primer PDF que se genera de cada uno es el que queda; si guardar falla, el documento se genera como
antes y se vuelve a intentar la próxima vez."""

import logging
from typing import Any, Protocol

from app.application.cliente_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoRechazado,
    PuertoAlmacenamiento,
)
from app.application.documento_poliza import generar_borrador, generar_contrato_firmado, generar_pdf
from app.application.servicio_documentos import TIPO_CONTRATO, TIPO_POLIZA, ServicioDocumentos
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
        documentos: ServicioDocumentos | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._ambiente_pruebas = ambiente_pruebas
        self._almacenamiento = almacenamiento
        self._documentos = documentos

    def obtener(self, poliza_id: str) -> dict[str, Any] | None:
        """La póliza con su número formateado, la prima y la cobertura; `None` si no existe."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        return {**poliza, "numero": numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])}

    def documento(self, poliza_id: str, asegurado: str | None = None) -> bytes | None:
        """El PDF de la póliza; `None` si la póliza no existe. Si ya está guardado, el que se guardó
        (descifrado en memoria); si no, se genera y se guarda para la próxima. El nombre del
        asegurado llega de quien lo pide y queda en el PDF guardado, que está cifrado. Lanza
        `DocumentoNoDisponible` si la imagen de la firma no se pudo bajar y `DocumentoAlterado` si
        el guardado no pasa la integridad."""
        poliza = self._repositorio.obtener_poliza(poliza_id)
        if poliza is None:
            return None
        propuesta_id = poliza["propuesta_id"]
        if self._documentos is not None:
            guardado = self._documentos.leer(propuesta_id, TIPO_POLIZA)
            if guardado is not None:
                return guardado
        propuesta = self._repositorio.obtener(propuesta_id)
        evidencia = self._repositorio.obtener_evidencia(propuesta_id)
        numero = numero_de_poliza(poliza["numero"], poliza["vigencia_desde"])
        imagen, texto = self._firma(propuesta_id, evidencia)
        pdf = generar_pdf(
            poliza,
            propuesta,
            evidencia,
            numero,
            asegurado,
            ambiente_pruebas=self._ambiente_pruebas,
            firma_imagen=imagen,
            firma_texto=texto,
        )
        if self._documentos is not None:
            self._documentos.guardar(propuesta_id, TIPO_POLIZA, pdf)
        return pdf

    def borrador(self, propuesta: dict[str, Any]) -> bytes:
        """El contrato a firmar, en PDF (W03-31)."""
        return generar_borrador(propuesta, ambiente_pruebas=self._ambiente_pruebas)

    def contrato(self, propuesta: dict[str, Any]) -> bytes:
        """`contrato.pdf`: el borrador mientras no esté firmado y, firmado, el contrato firmado
        guardado (o, si aún no se pudo guardar, generado y guardado ahora)."""
        evidencia = self._repositorio.obtener_evidencia(propuesta["id"])
        if evidencia is None:
            return self.borrador(propuesta)
        if self._documentos is not None:
            guardado = self._documentos.leer(propuesta["id"], TIPO_CONTRATO)
            if guardado is not None:
                return guardado
        imagen, texto = self._firma(propuesta["id"], evidencia)
        return self._contrato_firmado(propuesta, evidencia, imagen, texto)

    def guardar_contrato_firmado(
        self,
        propuesta: dict[str, Any],
        evidencia: dict[str, Any],
        imagen: bytes | None = None,
        texto: str | None = None,
    ) -> None:
        """Al firmar: genera el contrato firmado y lo guarda cifrado. Nunca lanza: si algo falla la
        firma ya está registrada y `contrato()` lo genera y lo guarda en la próxima consulta."""
        try:
            self._contrato_firmado(propuesta, evidencia, imagen, texto)
        except Exception as error:
            logger.warning("no se pudo generar el contrato firmado error=%s", type(error).__name__)

    def _contrato_firmado(
        self,
        propuesta: dict[str, Any],
        evidencia: dict[str, Any],
        imagen: bytes | None,
        texto: str | None,
    ) -> bytes:
        nombre = (self._repositorio.contacto(propuesta["id"]) or {}).get("nombre")
        pdf = generar_contrato_firmado(
            propuesta,
            evidencia,
            nombre,
            ambiente_pruebas=self._ambiente_pruebas,
            firma_imagen=imagen,
            firma_texto=texto,
        )
        if self._documentos is not None:
            self._documentos.guardar(propuesta["id"], TIPO_CONTRATO, pdf)
        return pdf

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
