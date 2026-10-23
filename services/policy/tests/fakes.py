"""Dobles de prueba de POLICY: en memoria, sin PostgreSQL ni RATING reales (mismo patrón que
PAYMENTS)."""

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.cliente_acl import ResultadoFirma, SolicitudFirma
from app.application.cliente_rating import OfertaNoEncontrada, RatingNoDisponible


def oferta_de_rating(**cambios: Any) -> dict[str, Any]:
    """La oferta tal como la guarda RATING (`GET /ofertas/{id}`)."""
    oferta: dict[str, Any] = {
        "id": "oferta-1",
        "tipo": "COMPLETA",
        "prima": {"valor": "185000", "moneda": "COP"},
        "cobertura": {"valor": "200000000", "moneda": "COP"},
        "clienteId": "cliente-opaco",
        "consentimientoId": "consentimiento-1",
        "plazoMeses": 60,
        "vigenteHasta": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    oferta.update(cambios)
    return oferta


class RatingFalso:
    def __init__(self, ofertas: dict[str, dict[str, Any]] | None = None, cae: bool = False) -> None:
        self.ofertas = ofertas if ofertas is not None else {"oferta-1": oferta_de_rating()}
        self.cae = cae
        self.llamadas = 0

    def obtener_oferta(self, oferta_id: str) -> dict[str, Any]:
        self.llamadas += 1
        if self.cae:
            raise RatingNoDisponible("caído")
        if oferta_id not in self.ofertas:
            raise OfertaNoEncontrada(oferta_id)
        return self.ofertas[oferta_id]


class RepositorioPropuestasFalso:
    """Fake de `RepositorioPropuestasPostgres`. `crear_o_obtener` usa un `Lock` para replicar la
    garantía del UNIQUE de Postgres: de varias llamadas concurrentes con la misma `oferta_id`, solo
    una crea la propuesta."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._por_oferta: dict[str, dict[str, Any]] = {}
        self._evidencias: dict[str, dict[str, Any]] = {}
        self.documentos_guardados: dict[str, str] = {}

    def obtener(self, propuesta_id: str) -> dict[str, Any] | None:
        for propuesta in self._por_oferta.values():
            if propuesta["id"] == propuesta_id:
                return dict(propuesta)
        return None

    def obtener_por_oferta(self, oferta_id: str) -> dict[str, Any] | None:
        propuesta = self._por_oferta.get(oferta_id)
        return dict(propuesta) if propuesta else None

    def obtener_evidencia(self, propuesta_id: str) -> dict[str, Any] | None:
        evidencia = self._evidencias.get(propuesta_id)
        return dict(evidencia) if evidencia else None

    def registrar_firma(self, propuesta_id: str, evidencia: dict[str, Any]) -> tuple[dict, bool]:
        with self._lock:
            existente = self._evidencias.get(propuesta_id)
            if existente is not None:
                return dict(existente), False
            guardada = {
                "id": evidencia["id"],
                "propuesta_id": propuesta_id,
                "documento_hash": evidencia["documento_hash"],
                "version_documento": evidencia["version_documento"],
                "firmante_id": evidencia["firmante_id"],
                "referencia_proveedor": evidencia["referencia_proveedor"],
                "firmada_en": evidencia["firmada_en"],
                "creado_en": datetime.now(UTC),
            }
            self._evidencias[propuesta_id] = guardada
            self.documentos_guardados[propuesta_id] = evidencia["documento"]
            for propuesta in self._por_oferta.values():
                if propuesta["id"] == propuesta_id and propuesta["estado"] == "ACEPTADA":
                    propuesta["estado"] = "EN_EMISION"
            return dict(guardada), True

    def crear_o_obtener(self, propuesta_id: str, datos: dict[str, Any]) -> tuple[dict, bool]:
        with self._lock:
            existente = self._por_oferta.get(datos["oferta_id"])
            if existente is not None:
                return dict(existente), False
            propuesta = {
                "id": propuesta_id,
                "oferta_id": datos["oferta_id"],
                "cliente_id": datos["cliente_id"],
                "consentimiento_id": datos.get("consentimiento_id"),
                "actor_usuario_id": datos.get("actor_usuario_id"),
                "estado": "ACEPTADA",
                "tipo": datos["tipo"],
                "prima": datos["prima"],
                "cobertura": datos["cobertura"],
                "plazo_meses": datos["plazo_meses"],
                "creado_en": datetime.now(UTC),
                "actualizado_en": datetime.now(UTC),
            }
            self._por_oferta[datos["oferta_id"]] = propuesta
            return dict(propuesta), True


class AclFalso:
    """Doble del ACL Worker: firma una sola vez por `id_firma`, como el proveedor real."""

    def __init__(self, resultados: list[ResultadoFirma] | None = None) -> None:
        self.resultados = list(resultados or [])
        self.solicitudes: list[SolicitudFirma] = []
        self._firmadas: dict[str, ResultadoFirma] = {}

    def firmar(self, solicitud: SolicitudFirma) -> ResultadoFirma:
        self.solicitudes.append(solicitud)
        if self.resultados:
            return self.resultados.pop(0)
        return self._firmadas.setdefault(
            solicitud.id_firma,
            ResultadoFirma(
                estado="firmada",
                referencia=f"fe-{solicitud.id_firma}",
                firmada_en="2026-10-22T12:00:00+00:00",
            ),
        )
