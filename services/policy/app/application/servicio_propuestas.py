"""Aceptar una oferta COMPLETA y guardarla como propuesta (HU-W03, KAN-209, CA-W03-01).

El precio sale de la oferta que RATING guardó, nunca de lo que mande el cliente. Una oferta que ya
tiene propuesta devuelve esa misma propuesta, aunque RATING ya la haya dejado vencer."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.application.cliente_rating import OfertaNoEncontrada, PuertoRating
from app.application.comisiones import resumir_ventas
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

    def listar(
        self,
        cliente_id: str | None,
        actor_usuario_id: str | None,
        estado_visible: str | None,
        pagina: int,
        tamano: int,
        actor_rol: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def polizas_resumen(self, ids: list[str]) -> dict[str, dict[str, Any]]: ...

    def propuestas_de_socio_por_mes(
        self, socio_id: str, desde: date, hasta: date
    ) -> dict[str, int]: ...

    def comisiones_de_socio(
        self, socio_id: str, desde: date, hasta: date
    ) -> list[dict[str, Any]]: ...


class TasaInvalida(Exception):
    """La tasa de comisión del socio no es un decimal entre 0 y 1."""


def tasa_valida(tasa: str | None) -> Decimal | None:
    if tasa is None:
        return None
    try:
        valor = Decimal(tasa)
    except InvalidOperation as error:
        raise TasaInvalida(tasa) from error
    if not Decimal(0) <= valor < Decimal(1):
        raise TasaInvalida(tasa)
    return valor


class ServicioPropuestas:
    def __init__(self, repositorio: PuertoRepositorioPropuestas, rating: PuertoRating) -> None:
        self._repositorio = repositorio
        self._rating = rating

    def obtener(self, propuesta_id: str) -> dict[str, Any] | None:
        return self._repositorio.obtener(propuesta_id)

    def listar(
        self,
        cliente_id: str | None,
        actor_usuario_id: str | None,
        estado_visible: str | None,
        pagina: int,
        tamano: int,
        actor_rol: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """W03-35: las propuestas con su póliza (número y vigencia) cuando existe. `actor_rol`
        acota a las de un rol (las de un socio: SOCIO + su id)."""
        propuestas, total = self._repositorio.listar(
            cliente_id, actor_usuario_id, estado_visible, pagina, tamano, actor_rol
        )
        polizas = self._repositorio.polizas_resumen(
            [p["poliza_id"] for p in propuestas if p.get("poliza_id")]
        )
        for propuesta in propuestas:
            propuesta["poliza"] = polizas.get(propuesta.get("poliza_id") or "")
        return propuestas, total

    def aceptar(
        self,
        oferta_id: str,
        actor_usuario_id: str | None,
        actor_rol: str | None = None,
        contacto: dict[str, str] | None = None,
        tasa_comision: str | None = None,
    ) -> dict[str, Any]:
        """`contacto` (nombre y correo) lo da el ASESOR y se guarda cifrado en la propuesta; sin él,
        el contacto del titular se lee de AUTH al usarlo (W03-25). Que el ASESOR lo mande lo exige
        el BFF.

        HU-W07: un SOCIO manda además `tasa_comision`, que queda congelada en la propuesta (W07-7);
        el BFF la lee de AUTH. Con otro rol se ignora."""
        tasa = tasa_valida(tasa_comision) if actor_rol == "SOCIO" else None
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
                # Las reglas con las que RATING fijó el precio (CA-W03-01); una oferta guardada
                # antes de que RATING la incluyera no la trae y queda en nulo.
                "version_reglas": oferta.get("versionReglas"),
                "actor_usuario_id": actor_usuario_id,
                "actor_rol": actor_rol,
                "contacto": contacto,
                "origen_contacto": "PROPUESTA" if contacto else ("AUTH" if actor_rol else None),
                "tasa_comision": tasa,
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

    def ventas_socio(self, socio_id: str, desde: date | None, hasta: date | None) -> dict[str, Any]:
        """HU-W07, `GET /v1/ventas`: propuestas aceptadas, pólizas emitidas, primas y comisión
        causada por mes. Por defecto, los últimos 12 meses."""
        hasta = hasta or datetime.now(UTC).date()
        desde = desde or (hasta.replace(day=1) - timedelta(days=335)).replace(day=1)
        if desde > hasta:
            raise ValueError("desde no puede ser posterior a hasta")
        resumen = resumir_ventas(
            self._repositorio.propuestas_de_socio_por_mes(socio_id, desde, hasta),
            self._repositorio.comisiones_de_socio(socio_id, desde, hasta),
        )
        return {"desde": desde, "hasta": hasta, **resumen}
