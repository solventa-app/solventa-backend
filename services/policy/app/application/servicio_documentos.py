"""Los documentos firmados, guardados y cifrados (KAN-26, T6, decisión D-1).

Dos documentos por propuesta, inmutables una vez guardados:

- `contrato-firmado`: al firmar, el contrato con la evidencia de la firma.
- `poliza-pdf`: la póliza emitida; el primer PDF que se genera de ella (el del correo o el de la
  primera consulta) es el que se guarda.

Cada uno se cifra aquí (`sobre_documentos.py`) y el contenedor cifrado va a ALMACENAMIENTO; en
POLICY solo quedan el id del archivo, la llave de datos **envuelta**, la huella y el tamaño
(`documentos_firmados`, migración `0010`).

**Nunca tumba el flujo (regla 4):** guardar es un registro de apoyo. Si ALMACENAMIENTO, el bucket o
KMS fallan, la firma y la emisión siguen y el PDF se genera como antes; el siguiente intento (la
próxima vez que se pida el documento) lo vuelve a guardar. Una **falla de integridad** al leer
(etiqueta GCM o huella que no coinciden) es otra cosa: no se sirve un documento alterado, se
registra como error y se responde que el documento no está disponible. Ningún log lleva el
contenido ni la llave."""

import logging
from typing import Any, Protocol

from app.application.cliente_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoRechazado,
    PuertoAlmacenamiento,
)
from app.application.identificadores import id_opaco
from app.application.sobre_documentos import (
    EnvolvedorLlaves,
    ErrorDeSobre,
    cifrar_documento,
    descifrar_documento,
)

logger = logging.getLogger("policy.documentos")

TIPO_CONTRATO = "contrato-firmado"
TIPO_POLIZA = "poliza-pdf"
# Un PDF de póliza pesa decenas de KB; el tope es el del catálogo de ALMACENAMIENTO (5 MB) más el
# encabezado del contenedor.
TAMANO_MAXIMO_CONTENEDOR = 5 * 1024 * 1024 + 64


class DocumentoAlterado(Exception):
    """El documento guardado no pasa la verificación de integridad: no se sirve."""


class PuertoRepositorioDocumentos(Protocol):
    def obtener_documento(self, propuesta_id: str, tipo: str) -> dict[str, Any] | None: ...

    def guardar_documento(
        self,
        propuesta_id: str,
        tipo: str,
        archivo_id: str,
        clave_envuelta: str,
        sha256: str,
        tamano_bytes: int,
    ) -> bool: ...

    def documentos_de(self, propuesta_id: str) -> list[dict[str, Any]]: ...


class ServicioDocumentos:
    def __init__(
        self,
        repositorio: PuertoRepositorioDocumentos,
        almacenamiento: PuertoAlmacenamiento,
        envolvedor: EnvolvedorLlaves,
    ) -> None:
        self._repo = repositorio
        self._almacen = almacenamiento
        self._envolvedor = envolvedor

    def existe(self, propuesta_id: str, tipo: str) -> bool:
        return self._repo.obtener_documento(propuesta_id, tipo) is not None

    def leer(self, propuesta_id: str, tipo: str) -> bytes | None:
        """El PDF en claro, descifrado en memoria; `None` si no está guardado o no se pudo leer
        ahora (el llamador lo regenera). Lanza `DocumentoAlterado` si no pasa la integridad."""
        fila = self._repo.obtener_documento(propuesta_id, tipo)
        if fila is None:
            return None
        try:
            contenedor = self._almacen.bajar(
                fila["archivo_id"], tamano_max=TAMANO_MAXIMO_CONTENEDOR
            )
            return descifrar_documento(
                contenedor,
                fila["clave_envuelta"],
                propuesta_id,
                tipo,
                self._envolvedor,
                fila["sha256"],
            )
        except ErrorDeSobre as error:
            logger.error(
                "documento guardado alterado propuesta=%s tipo=%s", id_opaco(propuesta_id), tipo
            )
            raise DocumentoAlterado(tipo) from error
        except (AlmacenamientoNoDisponible, ArchivoRechazado) as error:
            logger.warning(
                "no se pudo leer el documento guardado propuesta=%s tipo=%s error=%s",
                id_opaco(propuesta_id),
                tipo,
                type(error).__name__,
            )
        except Exception as error:  # KMS u otra falla de disponibilidad: se regenera
            logger.warning(
                "no se pudo descifrar el documento guardado propuesta=%s tipo=%s error=%s",
                id_opaco(propuesta_id),
                tipo,
                type(error).__name__,
            )
        return None

    def guardar(self, propuesta_id: str, tipo: str, pdf: bytes) -> bool:
        """Cifra y guarda el PDF una sola vez por propuesta y tipo. Devuelve si ya quedó guardado
        (ahora o antes); `False` si no se pudo, sin lanzar nada: el flujo no depende de esto."""
        try:
            if self._repo.obtener_documento(propuesta_id, tipo) is not None:
                return True
            cifrado = cifrar_documento(pdf, propuesta_id, tipo, self._envolvedor)
            archivo_id = self._almacen.subir_documento(tipo, propuesta_id, cifrado.contenedor)
            self._repo.guardar_documento(
                propuesta_id,
                tipo,
                archivo_id,
                cifrado.clave_envuelta,
                cifrado.sha256,
                cifrado.tamano_bytes,
            )
        except Exception as error:
            logger.warning(
                "no se pudo guardar el documento propuesta=%s tipo=%s error=%s",
                id_opaco(propuesta_id),
                tipo,
                type(error).__name__,
            )
            return False
        logger.info("documento guardado propuesta=%s tipo=%s", id_opaco(propuesta_id), tipo)
        return True

    def archivos_de(self, propuesta_id: str) -> list[str]:
        """Los ids de archivo de los documentos de la propuesta (para retenerlos al purgar)."""
        return [d["archivo_id"] for d in self._repo.documentos_de(propuesta_id)]
