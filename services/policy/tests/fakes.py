"""Dobles de prueba de POLICY: en memoria, sin PostgreSQL ni RATING reales (mismo patrón que
PAYMENTS)."""

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.cliente_acl import (
    ResultadoFirma,
    ResultadoNotificacion,
    SolicitudFirma,
    SolicitudNotificacion,
)
from app.application.cliente_payments import (
    CobroEnConflicto,
    OrdenPrimerCobro,
    PaymentsNoDisponible,
)
from app.application.cliente_payments import ResultadoCobro as ResultadoCobroPayments
from app.application.cliente_rating import OfertaNoEncontrada, RatingNoDisponible
from app.application.cliente_under import ResultadoDecision, UnderNoDisponible


def oferta_de_rating(**cambios: Any) -> dict[str, Any]:
    """La oferta tal como la guarda RATING (`GET /ofertas/{id}`)."""
    oferta: dict[str, Any] = {
        "id": "oferta-1",
        "tipo": "COMPLETA",
        "prima": {"valor": "185000", "moneda": "COP"},
        "cobertura": {"valor": "200000000", "moneda": "COP"},
        "clienteId": "cliente-opaco",
        "consentimientoId": "consentimiento-1",
        "versionReglas": "v1",
        "plazoMeses": 60,
        "perfilRiesgo": {"score": 700, "nivelRiesgo": "BAJO", "fechaVigencia": "2026-12-01"},
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
        self._polizas: dict[str, dict[str, Any]] = {}
        self._secuencia = 0

    def obtener(self, propuesta_id: str) -> dict[str, Any] | None:
        for propuesta in self._por_oferta.values():
            if propuesta["id"] == propuesta_id:
                return dict(propuesta)
        return None

    def obtener_por_oferta(self, oferta_id: str) -> dict[str, Any] | None:
        propuesta = self._por_oferta.get(oferta_id)
        return dict(propuesta) if propuesta else None

    def _propuesta(self, propuesta_id: str) -> dict[str, Any] | None:
        for propuesta in self._por_oferta.values():
            if propuesta["id"] == propuesta_id:
                return propuesta
        return None

    def registrar_intento(self, propuesta_id: str) -> int:
        with self._lock:
            propuesta = self._propuesta(propuesta_id)
            propuesta["intentos_emision"] += 1
            return propuesta["intentos_emision"]

    def reiniciar_intentos(self, propuesta_id: str) -> None:
        with self._lock:
            self._propuesta(propuesta_id)["intentos_emision"] = 0

    def listar_para_reconciliar(
        self, estados: tuple[str, ...], dias: int, limite: int
    ) -> list[str]:
        with self._lock:
            ids = [p["id"] for p in self._por_oferta.values() if p["estado"] in estados]
        return ids[:limite]

    def cambiar_estado(
        self, propuesta_id: str, nuevo: str, desde: tuple[str, ...], motivo: str | None = None
    ) -> bool:
        with self._lock:
            propuesta = self._propuesta(propuesta_id)
            if propuesta["estado"] not in desde:
                return False
            propuesta["estado"] = nuevo
            propuesta["motivo_estado"] = motivo
            return True

    def emitir_poliza(
        self,
        propuesta_id: str,
        cliente_id: str,
        vigencia_desde: Any,
        vigencia_hasta: Any,
        desde: tuple[str, ...] = ("EN_EMISION", "PENDIENTE_PAGO"),
    ) -> tuple[dict[str, Any] | None, bool]:
        with self._lock:
            propuesta = self._propuesta(propuesta_id)
            if propuesta["estado"] not in desde:
                existente = self._polizas.get(propuesta_id)
                return (dict(existente) if existente else None), False
            self._secuencia += 1
            poliza = {
                "id": f"poliza-{self._secuencia}",
                "propuesta_id": propuesta_id,
                "numero": self._secuencia,
                "cliente_id": cliente_id,
                "estado": "EMITIDA",
                "vigencia_desde": vigencia_desde,
                "vigencia_hasta": vigencia_hasta,
                "notificada_en": None,
            }
            self._polizas[propuesta_id] = poliza
            propuesta["estado"] = "EMITIDA"
            propuesta["poliza_id"] = poliza["id"]
            return dict(poliza), True

    def obtener_poliza(self, poliza_id: str) -> dict[str, Any] | None:
        for propuesta_id, poliza in self._polizas.items():
            if poliza["id"] == poliza_id:
                propuesta = self._propuesta(propuesta_id)
                evidencia = self._evidencias.get(propuesta_id)
                return {
                    **poliza,
                    "prima": propuesta["prima"],
                    "cobertura": propuesta["cobertura"],
                    "plazo_meses": propuesta["plazo_meses"],
                    "actor_usuario_id": propuesta["actor_usuario_id"],
                    "firmante_id": evidencia["firmante_id"] if evidencia else None,
                }
        return None

    def marcar_notificada(self, poliza_id: str) -> None:
        for poliza in self._polizas.values():
            if poliza["id"] == poliza_id and poliza["notificada_en"] is None:
                poliza["notificada_en"] = datetime.now(UTC)

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
                "version_reglas": datos.get("version_reglas"),
                "actor_usuario_id": datos.get("actor_usuario_id"),
                "estado": "ACEPTADA",
                "poliza_id": None,
                "intentos_emision": 0,
                "motivo_estado": None,
                "nivel_riesgo": datos.get("nivel_riesgo"),
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


class UnderFalso:
    def __init__(self, decision: str = "APROBADA", motivos: list[str] | None = None, cae=False):
        self.decision = decision
        self.motivos = motivos or []
        self.cae = cae
        self.llamadas: list[tuple] = []

    def decidir(self, propuesta_id, cliente_id, nivel_riesgo, cobertura) -> ResultadoDecision:
        self.llamadas.append((propuesta_id, cliente_id, nivel_riesgo, cobertura))
        if self.cae:
            raise UnderNoDisponible("caído")
        return ResultadoDecision(self.decision, list(self.motivos))


class PaymentsFalso:
    """Doble de PAYMENTS: idempotente por `id_intento_cobro`, como el real. `estados` es la
    secuencia de estados que devuelve en cada llamada (el último se repite)."""

    def __init__(self, estados: list[str] | None = None, cae=False, conflicto=False):
        self.estados = list(estados or ["cobrado"])
        self.cae = cae
        self.conflicto = conflicto
        self.ordenes: list[OrdenPrimerCobro] = []

    def crear_cobro(self, orden: OrdenPrimerCobro) -> ResultadoCobroPayments:
        self.ordenes.append(orden)
        if self.cae:
            raise PaymentsNoDisponible("caído")
        if self.conflicto:
            raise CobroEnConflicto(orden.id_intento_cobro)
        estado = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        motivo = "fondos_insuficientes" if estado == "rechazado" else None
        return ResultadoCobroPayments(estado, f"cobro-{orden.id_intento_cobro[:8]}", motivo)


class PlanificadorFalso:
    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.encolados: list[tuple[str, float]] = []

    def encolar(self, propuesta_id: str, demora_segundos: float = 0) -> None:
        self.encolados.append((propuesta_id, demora_segundos))
        if self.falla:
            raise RuntimeError("Cloud Tasks caído")


class BusFalso:
    def __init__(self) -> None:
        self.eventos: list[dict[str, Any]] = []

    def publicar(self, evento: dict[str, Any]) -> None:
        self.eventos.append(evento)


class NotificadorFalso:
    """Doble del ACL para los avisos: idempotente por `id_notificacion`, como el proveedor real.
    `resultados` es una secuencia de estados (`enviada`/`pendiente`); el último se repite."""

    def __init__(self, resultados: list[str] | None = None) -> None:
        self.resultados = list(resultados or ["enviada"])
        self.solicitudes: list[SolicitudNotificacion] = []
        self.enviadas: dict[str, SolicitudNotificacion] = {}

    def notificar(self, solicitud: SolicitudNotificacion) -> ResultadoNotificacion:
        self.solicitudes.append(solicitud)
        estado = self.resultados.pop(0) if len(self.resultados) > 1 else self.resultados[0]
        if estado == "enviada":
            self.enviadas.setdefault(solicitud.id_notificacion, solicitud)
            return ResultadoNotificacion("enviada", f"nt-{solicitud.id_notificacion}")
        return ResultadoNotificacion("pendiente", motivo="acl_no_disponible")
