"""Siniestros paramétricos (KAN-259, HU-M04, Fase 4, D8): cruza un `evento.parametrico.recibido`
(clima o vuelo, publicado por el ACL Worker) contra las coberturas paramétricas vigentes de POLICY
y, si el umbral se cumple, paga la indemnización de una vez.

No pasa por `ServicioSiniestros.reportar()` (es automático, no lo reporta un cliente): el caso nace
ya `APROBADO`, con el mismo espíritu de `ServicioSiniestros.aprobar()` — primero la orden de pago en
PAYMENTS (idempotente por `siniestro_id`), luego el siniestro y su resolución en una sola
transacción (W04-9: si el bus de eventos falla después, no se revierte nada ya guardado).

**Idempotencia por `(eventoId, polizaId)`:** el `siniestro_id` es `uuid5` determinista de esos dos
valores. Un reintento del mismo evento (Pub/Sub puede reentregar) calcula el mismo id; el índice
único de `_id` en `siniestros` rechaza el duplicado (`DuplicateKeyError`) y el llamador lee el caso
que ya existe, sin pedir otra orden de pago ni crear otro siniestro."""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.application.bus_eventos import (
    PuertoBusEventos,
    evento_aprobado,
    evento_push_indemnizacion,
)
from app.application.clientes import ServicioNoDisponible, SolicitudRechazada
from app.application.identificadores import id_opaco

logger = logging.getLogger("claims.parametricos")

# Espacio de nombres fijo, solo para que `uuid5` sea determinista entre corridas (no es un secreto).
_NAMESPACE = uuid.UUID("c259c259-0000-4000-8000-000000000259")

NOTIFICACION_APROBACION = "Enviada al asegurado y a la entidad acreedora"
ENTIDAD_POR_CONFIRMAR = "Por confirmar con el asegurado"
ACTOR_SISTEMA = "SISTEMA"

DESCRIPCION_POR_TIPO = {
    "VUELO": (
        "Siniestro paramétrico automático: vuelo con retraso o cancelación sobre el umbral "
        "de la póliza."
    ),
    "CLIMA": "Siniestro paramétrico automático: variable climática sobre el umbral de la póliza.",
}
COBERTURA_APLICADA_POR_TIPO = {
    "VUELO": "Cobertura paramétrica de vuelo",
    "CLIMA": "Cobertura paramétrica de clima",
}


class PuertoPolicyParametrico(Protocol):
    def obtener_poliza(self, poliza_id: str) -> dict[str, Any]: ...

    def coberturas_de_vuelo(self, numero_vuelo: str, fecha: str) -> list[dict[str, Any]]: ...

    def coberturas_de_celda(
        self, latitud: float, longitud: float, variable: str
    ) -> list[dict[str, Any]]: ...


class PuertoPayments(Protocol):
    def ordenar_indemnizacion(
        self, siniestro_id: str, poliza_id: str, monto: str, moneda: str
    ) -> dict[str, Any]: ...


class PuertoRepositorioParametricos(Protocol):
    def obtener(self, siniestro_id: str, primaria: bool = False) -> dict | None: ...

    def siguiente_radicado(self, anio: int) -> str: ...

    def crear_parametrico(self, siniestro: dict, resolucion: dict, ahora: datetime) -> bool: ...


def siniestro_id_determinista(evento_id: str, poliza_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{evento_id}:{poliza_id}"))


def _a_fecha(valor: str | datetime) -> datetime:
    """POLICY devuelve `vigencia_desde`/`vigencia_hasta` como texto ISO por HTTP; las pruebas
    pueden pasar `datetime` directo (igual que el resto de CLAIMS)."""
    return valor if isinstance(valor, datetime) else datetime.fromisoformat(valor)


class ServicioParametricos:
    def __init__(
        self,
        repositorio: PuertoRepositorioParametricos,
        policy: PuertoPolicyParametrico,
        payments: PuertoPayments,
        bus: PuertoBusEventos | None = None,
        reloj=lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repositorio
        self._policy = policy
        self._payments = payments
        self._bus = bus
        self._reloj = reloj

    def procesar(self, evento: dict[str, Any]) -> dict[str, int]:
        """Cruza el evento contra las coberturas vigentes y crea un siniestro paramétrico por cada
        póliza que califica. `{"creados": N, "revisadas": M}`; ambos en 0 si no hay nada que hacer
        (evento válido pero sin pólizas o bajo el umbral: regla #4, nunca es un error)."""
        datos = evento["datos"]
        medido_en = datetime.fromisoformat(datos["medidoEn"])
        coberturas = self._coberturas_que_calzan(datos)
        creados = 0
        for cobertura in coberturas:
            desde = _a_fecha(cobertura["vigencia_desde"])
            hasta = _a_fecha(cobertura["vigencia_hasta"])
            if not (desde <= medido_en <= hasta):
                continue
            if self._crear_siniestro(evento, cobertura, medido_en, hasta):
                creados += 1
        return {"creados": creados, "revisadas": len(coberturas)}

    def _coberturas_que_calzan(self, datos: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            if datos["tipo"] == "VUELO":
                return self._coberturas_vuelo(datos)
            return self._coberturas_clima(datos)
        except SolicitudRechazada:
            # POLICY rechazó la consulta (p. ej. parámetros inválidos): nada que cruzar.
            return []

    def _coberturas_vuelo(self, datos: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            numero_vuelo, fecha = datos["clave"].split(":", 1)
        except ValueError:
            logger.warning("clave de vuelo con formato inesperado: %s", id_opaco(datos["clave"]))
            return []
        coberturas = self._policy.coberturas_de_vuelo(numero_vuelo, fecha)
        cancelado = bool(datos.get("cancelado"))
        return [
            c
            for c in coberturas
            if cancelado or datos["valor"] >= (c.get("umbral_retraso_min") or 0)
        ]

    def _coberturas_clima(self, datos: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            lat_s, lon_s = datos["clave"].split(",", 1)
            latitud, longitud = float(lat_s), float(lon_s)
        except (ValueError, KeyError):
            logger.warning("clave de celda con formato inesperado: %s", id_opaco(datos["clave"]))
            return []
        variable = datos.get("variable")
        if not variable:
            return []
        coberturas = self._policy.coberturas_de_celda(latitud, longitud, variable)
        try:
            valor = Decimal(str(datos["valor"]))
            return [c for c in coberturas if c.get("umbral") and valor >= Decimal(c["umbral"])]
        except InvalidOperation:
            return []

    def _crear_siniestro(
        self,
        evento: dict[str, Any],
        cobertura: dict[str, Any],
        medido_en: datetime,
        vigencia_hasta: datetime,
    ) -> bool:
        poliza_id = cobertura["poliza_id"]
        sid = siniestro_id_determinista(evento["eventId"], poliza_id)
        existente = self._repo.obtener(sid, primaria=True)
        if existente is not None:
            logger.info(
                "evento=%s poliza=%s ya tenía siniestro paramétrico: no se duplica",
                evento["eventId"],
                id_opaco(poliza_id),
            )
            return False
        try:
            orden = self._payments.ordenar_indemnizacion(
                sid,
                poliza_id,
                cobertura["monto_fijo"]["valor"],
                cobertura["monto_fijo"]["moneda"],
            )
        except (ServicioNoDisponible, SolicitudRechazada) as error:
            logger.warning(
                "siniestro paramétrico=%s orden de pago no registrada: %s", id_opaco(sid), error
            )
            raise
        ahora = self._reloj()
        tipo = cobertura["tipo"]
        datos = evento["datos"]
        siniestro = {
            "id": sid,
            "radicado": self._repo.siguiente_radicado(ahora.year),
            "poliza_id": poliza_id,
            "estado": "APROBADO",
            "asegurado": {"nombre": "Cobertura paramétrica", "documento": "N/A"},
            "cobertura_aplicada": COBERTURA_APLICADA_POR_TIPO[tipo],
            "entidad_acreedora": ENTIDAD_POR_CONFIRMAR,
            "ocurrido_en": medido_en,
            "notificado_en": ahora,
            "ubicacion": {"tipo": tipo, "clave": datos["clave"]},
            "descripcion": DESCRIPCION_POR_TIPO[tipo],
            "suma_reclamada": cobertura["monto_fijo"],
            "vence_dictamen_en": ahora,
            "tipo_siniestro_auto": None,
            "evento_parametrico_id": evento["eventId"],
            "origen": "PARAMETRICO",
            # KAN-259 Fase 5 (BFF `detalleEvento`): el valor crudo del evento y el umbral de la
            # cobertura que se cruzó, para que el BFF componga "+3h40" o "62 mm" sin tener que
            # volver a consultar POLICY ni al ACL. No son datos personales (regla #6): se guardan
            # en claro, sin cifrar.
            "evento_valor": datos.get("valor"),
            "evento_umbral": cobertura.get("umbral_retraso_min") or cobertura.get("umbral"),
            "evento_variable": datos.get("variable"),
            "evento_cancelado": bool(datos.get("cancelado")) if tipo == "VUELO" else None,
            # KAN-259 Fase 5 (BFF `polizaAnterior`): la vigencia de la cobertura al momento del
            # evento, ya normalizada a `datetime` (igual que `desde`/`hasta` en `procesar`).
            "vigencia_hasta_cobertura": vigencia_hasta,
        }
        resolucion = {
            "id": str(uuid.uuid4()),
            "siniestro_id": sid,
            "tipo": "APROBACION",
            "notificacion": NOTIFICACION_APROBACION,
            "indemnizacion": cobertura["monto_fijo"],
            "metodo_transferencia": orden.get("metodo_transferencia"),
            "plazo_dispersion": orden.get("plazo_dispersion"),
            "orden_pago_id": orden["id"],
            "automatica": True,
            "resuelta_en": ahora,
            "actor_usuario_id": ACTOR_SISTEMA,
        }
        if not self._repo.crear_parametrico(siniestro, resolucion, ahora):
            # Otra réplica del mismo evento ganó la carrera entre el chequeo y la escritura.
            logger.info("siniestro paramétrico=%s creado por otra réplica", id_opaco(sid))
            return False
        logger.info(
            "siniestro paramétrico=%s aprobado poliza=%s orden=%s",
            id_opaco(sid),
            id_opaco(poliza_id),
            id_opaco(orden["id"]),
        )
        self._publicar(siniestro, resolucion)
        return True

    def _publicar(self, siniestro: dict[str, Any], resolucion: dict[str, Any]) -> None:
        """Después de guardar: si el bus falla, el siniestro y el pago ya quedaron hechos (W04-9,
        el mismo criterio que `ServicioSiniestros`)."""
        if self._bus is None:
            return
        self._bus.publicar(evento_aprobado(siniestro, resolucion, ACTOR_SISTEMA))
        usuario_id = self._usuario_del_titular(siniestro["poliza_id"])
        if usuario_id is None:
            logger.warning(
                "siniestro paramétrico=%s sin usuario al que avisar: no se pide el push",
                id_opaco(siniestro["id"]),
            )
            return
        self._bus.publicar(
            evento_push_indemnizacion(usuario_id, siniestro["id"], resolucion["indemnizacion"])
        )

    def _usuario_del_titular(self, poliza_id: str) -> str | None:
        """`notificacion.push-solicitada` necesita un `usuarioId` de AUTH: el de quien firmó la
        póliza (el CLIENTE titular) o, si no firmó, quien la aceptó. Decisión de diseño (el plan no
        lo especifica): mismo criterio que `ServicioSiniestros._poliza_del_cliente` usa para decidir
        de quién es la póliza."""
        try:
            poliza = self._policy.obtener_poliza(poliza_id)
        except (ServicioNoDisponible, SolicitudRechazada) as error:
            logger.warning(
                "no se pudo leer la póliza=%s para el push: %s", id_opaco(poliza_id), error
            )
            return None
        return poliza.get("firmante_id") or poliza.get("actor_usuario_id")
