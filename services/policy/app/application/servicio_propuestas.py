"""Aceptar una oferta COMPLETA y guardarla como propuesta (HU-W03, KAN-209, CA-W03-01).

El precio sale de la oferta que RATING guardó, nunca de lo que mande el cliente. Una oferta que ya
tiene propuesta devuelve esa misma propuesta, aunque RATING ya la haya dejado vencer."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.cliente_rating import OfertaNoEncontrada, PuertoRating
from app.application.identificadores import id_opaco

logger = logging.getLogger("policy.propuestas")

OFERTA_PRELIMINAR = "OFERTA_PRELIMINAR"
OFERTA_VENCIDA = "OFERTA_VENCIDA"
OFERTA_NO_ENCONTRADA = "OFERTA_NO_ENCONTRADA"


class OfertaNoAceptable(Exception):
    """La oferta no se puede aceptar. `motivo`: un `MotivoErrorAceptacion` del contrato."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


class PuertoRepositorioPropuestas(Protocol):
    def obtener(self, propuesta_id: str) -> dict[str, Any] | None: ...

    def obtener_por_oferta(self, oferta_id: str) -> dict[str, Any] | None: ...

    def crear_o_obtener(self, propuesta_id: str, datos: dict[str, Any]) -> tuple[dict, bool]: ...


class ServicioPropuestas:
    def __init__(self, repositorio: PuertoRepositorioPropuestas, rating: PuertoRating) -> None:
        self._repositorio = repositorio
        self._rating = rating

    def obtener(self, propuesta_id: str) -> dict[str, Any] | None:
        return self._repositorio.obtener(propuesta_id)

    def aceptar(self, oferta_id: str, actor_usuario_id: str | None) -> dict[str, Any]:
        existente = self._repositorio.obtener_por_oferta(oferta_id)
        if existente is not None:
            return existente

        try:
            oferta = self._rating.obtener_oferta(oferta_id)
        except OfertaNoEncontrada as error:
            raise OfertaNoAceptable(OFERTA_NO_ENCONTRADA) from error
        if oferta.get("tipo") != "COMPLETA" or not oferta.get("prima"):
            raise OfertaNoAceptable(OFERTA_PRELIMINAR)
        if datetime.fromisoformat(oferta["vigenteHasta"]) <= datetime.now(UTC):
            raise OfertaNoAceptable(OFERTA_VENCIDA)

        propuesta, creada = self._repositorio.crear_o_obtener(
            str(uuid.uuid4()),
            {
                "oferta_id": oferta_id,
                "cliente_id": oferta["clienteId"],
                "consentimiento_id": oferta.get("consentimientoId"),
                "actor_usuario_id": actor_usuario_id,
                "tipo": oferta["tipo"],
                "prima": oferta["prima"],
                "cobertura": oferta["cobertura"],
                "plazo_meses": oferta.get("plazoMeses"),
                # Lo necesita UNDER al decidir; si la oferta no lo trae, UNDER la manda a revisión.
                "nivel_riesgo": (oferta.get("perfilRiesgo") or {}).get("nivelRiesgo"),
            },
        )
        logger.info(
            "propuesta %s cliente=%s oferta=%s",
            "creada" if creada else "ya existía",
            id_opaco(oferta["clienteId"]),
            id_opaco(oferta_id),
        )
        return propuesta
