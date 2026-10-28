"""Retención del contacto de quien no es cliente (HU-W03, W03-26).

El contacto que dio el ASESOR, el texto y la imagen de la firma siguen al consentimiento. Si el
consentimiento **vence o se revoca antes de emitir**, la propuesta pasa a CANCELADA, se borran el
nombre, el correo y el texto de la firma, se anulan sus enlaces y se pide a ALMACENAMIENTO la
retención de la imagen (se borra en un día como máximo). La evidencia conserva la huella, la versión
y el sello. **Con la póliza emitida el contacto se conserva por el contrato.**

- Revocación: el BFF avisa a POLICY al revocar (`consentimiento_revocado`, idempotente).
- Vencimiento: una pasada diaria con Cloud Scheduler (`purgar_vencidos`) le pregunta a AUTH por el
  consentimiento de cada propuesta sin emitir que aún guarda datos. Si AUTH no responde, no se borra
  nada a ciegas: la propuesta se retoma en la próxima pasada."""

import logging
from typing import Any, Protocol

from app.application.avisos import AvisosNulos, PuertoAvisos
from app.application.cliente_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoRechazado,
    PuertoAlmacenamiento,
)
from app.application.cliente_auth import AuthNoDisponible, PuertoConsentimientos
from app.application.identificadores import id_opaco

logger = logging.getLogger("policy.purga")


class PuertoRepositorioPurga(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def cancelar_y_borrar_contacto(self, propuesta_id: str) -> str | None: ...

    def propuestas_del_consentimiento(
        self, consentimiento_id: str, titular_id: str
    ) -> list[str]: ...

    def candidatas_a_purga(self, limite: int) -> list[dict[str, Any]]: ...


class ServicioPurga:
    def __init__(
        self,
        repositorio: PuertoRepositorioPurga,
        consentimientos: PuertoConsentimientos,
        almacenamiento: PuertoAlmacenamiento | None,
        avisos: PuertoAvisos | None = None,
        lote: int = 200,
    ) -> None:
        self._repo = repositorio
        self._consentimientos = consentimientos
        self._almacen = almacenamiento
        self._avisos = avisos or AvisosNulos()
        self._lote = lote

    def consentimiento_revocado(self, titular_id: str, consentimiento_id: str) -> int:
        """Purga las propuestas sin emitir de ese consentimiento y titular. Devuelve cuántas
        tocó."""
        ids = self._repo.propuestas_del_consentimiento(consentimiento_id, titular_id)
        for propuesta_id in ids:
            self._purgar(propuesta_id)
        if ids:
            logger.info("consentimiento revocado: %s propuestas purgadas", len(ids))
        return len(ids)

    def purgar_vencidos(self) -> dict[str, int]:
        resumen = {"revisadas": 0, "purgadas": 0, "sin_respuesta": 0}
        for candidata in self._repo.candidatas_a_purga(self._lote):
            resumen["revisadas"] += 1
            try:
                vigente = self._consentimientos.consentimiento_vigente(
                    candidata["consentimiento_id"]
                )
            except AuthNoDisponible:
                resumen["sin_respuesta"] += 1
                continue
            if not vigente:
                self._purgar(candidata["id"])
                resumen["purgadas"] += 1
        logger.info("purga de contactos %s", resumen)
        return resumen

    def _purgar(self, propuesta_id: str) -> None:
        imagen = self._repo.cancelar_y_borrar_contacto(propuesta_id)
        if imagen and self._almacen is not None:
            try:
                self._almacen.retener(imagen)
            except (ArchivoRechazado, AlmacenamientoNoDisponible) as error:
                # El id queda en la evidencia; la próxima purga no lo retoma (contacto ya borrado),
                # así que se registra para revisarlo. La regla del bucket no aplica sin retención.
                logger.warning(
                    "no se pudo pedir la retención de la firma propuesta=%s error=%s",
                    id_opaco(propuesta_id),
                    type(error).__name__,
                )
        propuesta = self._repo.obtener(propuesta_id)
        if propuesta is not None:
            self._avisos.publicar(propuesta, propuesta["estado"])
