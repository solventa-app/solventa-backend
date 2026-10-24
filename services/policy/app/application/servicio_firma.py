"""Firmar la propuesta con el proveedor, vía el ACL Worker, y guardar la evidencia de no repudio
(HU-W03, KAN-211, CA-W03-02 y CA-W03-03).

Orden de las comprobaciones: la propuesta existe; si ya tiene evidencia se devuelve esa misma
(firmar dos veces no firma dos veces); solo se firma una propuesta `ACEPTADA`; el cliente debe
autorizar el cobro; y la versión del documento que vio debe ser la vigente. Solo entonces se llama
al ACL. El proveedor es idempotente por `id_firma` (= el id de la propuesta), así que reintentar
tras un corte por tiempo devuelve la misma evidencia."""

import logging
import uuid
from datetime import datetime
from typing import Any, Protocol

from app.application.cliente_acl import PuertoClienteAcl, SolicitudFirma
from app.application.documento_firma import (
    VERSION_DOCUMENTO,
    DocumentoNoGenerable,
    construir_documento,
    hash_documento,
    serializar,
)
from app.application.identificadores import id_opaco
from app.application.planificador import PuertoPlanificador

logger = logging.getLogger("policy.firma")

COBRO_NO_AUTORIZADO = "COBRO_NO_AUTORIZADO"
VERSION_DOCUMENTO_DESACTUALIZADA = "VERSION_DOCUMENTO_DESACTUALIZADA"
PROPUESTA_NO_FIRMABLE = "PROPUESTA_NO_FIRMABLE"
SERVICIO_FIRMA_NO_DISPONIBLE = "SERVICIO_FIRMA_NO_DISPONIBLE"


class PropuestaNoEncontrada(Exception):
    pass


class FirmaNoPermitida(Exception):
    """No se puede firmar. `motivo`: un `MotivoErrorFirma` del contrato."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class PuertoRepositorioFirmas(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def registrar_firma(
        self, propuesta_id: str, evidencia: dict[str, Any]
    ) -> tuple[dict, bool]: ...


class ServicioFirma:
    def __init__(
        self,
        repositorio: PuertoRepositorioFirmas,
        acl: PuertoClienteAcl,
        planificador: PuertoPlanificador | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._acl = acl
        self._planificador = planificador

    def _encolar_emision(self, propuesta_id: str) -> None:
        """Pide que se emita. Nunca tumba una firma ya registrada: si falla, volver a firmar (que es
        idempotente) la vuelve a encolar."""
        if self._planificador is None:
            return
        try:
            self._planificador.encolar(propuesta_id)
        except Exception as error:
            logger.warning(
                "no se pudo encolar la emisión propuesta=%s error=%s",
                id_opaco(propuesta_id),
                type(error).__name__,
            )

    def documento(self, propuesta_id: str) -> dict[str, Any]:
        """Lo que el cliente lee antes de firmar (W03-02a y W03-02b)."""
        propuesta = self._repositorio.obtener(propuesta_id)
        if propuesta is None:
            raise PropuestaNoEncontrada(propuesta_id)
        try:
            return construir_documento(propuesta)
        except DocumentoNoGenerable as error:
            raise FirmaNoPermitida(PROPUESTA_NO_FIRMABLE) from error

    def firmar(
        self,
        propuesta_id: str,
        autoriza_cobro: bool,
        version_documento: str,
        firmante_id: str,
    ) -> tuple[dict[str, Any], str]:
        """La evidencia y el estado de la propuesta después de firmar."""
        propuesta = self._repositorio.obtener(propuesta_id)
        if propuesta is None:
            raise PropuestaNoEncontrada(propuesta_id)

        existente = self._repositorio.obtener_evidencia(propuesta_id)
        if existente is not None:
            if propuesta["estado"] == "EN_EMISION":
                self._encolar_emision(propuesta_id)  # quedó sin avanzar: se vuelve a pedir
            return existente, propuesta["estado"]

        if propuesta["estado"] != "ACEPTADA":
            raise FirmaNoPermitida(PROPUESTA_NO_FIRMABLE)
        if not autoriza_cobro:
            raise FirmaNoPermitida(COBRO_NO_AUTORIZADO)
        if version_documento != VERSION_DOCUMENTO:
            raise FirmaNoPermitida(VERSION_DOCUMENTO_DESACTUALIZADA)
        try:
            texto = serializar(construir_documento(propuesta))
        except DocumentoNoGenerable as error:
            raise FirmaNoPermitida(PROPUESTA_NO_FIRMABLE) from error
        documento_hash = hash_documento(texto)

        resultado = self._acl.firmar(
            SolicitudFirma(
                id_firma=propuesta_id,
                documento_hash=documento_hash,
                version_documento=VERSION_DOCUMENTO,
                firmante_id=firmante_id,
            )
        )
        if resultado.estado != "firmada" or not resultado.referencia or not resultado.firmada_en:
            logger.warning("firma pendiente motivo=%s", resultado.motivo)
            raise FirmaNoPermitida(SERVICIO_FIRMA_NO_DISPONIBLE)

        evidencia, creada = self._repositorio.registrar_firma(
            propuesta_id,
            {
                "id": str(uuid.uuid4()),
                "documento": texto,
                "documento_hash": documento_hash,
                "version_documento": VERSION_DOCUMENTO,
                "firmante_id": firmante_id,
                "consentimiento_id": propuesta.get("consentimiento_id"),
                "referencia_proveedor": resultado.referencia,
                "firmada_en": datetime.fromisoformat(resultado.firmada_en),
            },
        )
        logger.info(
            "firma %s propuesta=%s firmante=%s",
            "registrada" if creada else "ya existía",
            id_opaco(propuesta_id),
            id_opaco(firmante_id),
        )
        if creada:
            self._encolar_emision(propuesta_id)
            return evidencia, "EN_EMISION"
        # Otra petición firmó primero y ya cambió el estado: se vuelve a leer, no se supone.
        return evidencia, self._repositorio.obtener(propuesta_id)["estado"]
