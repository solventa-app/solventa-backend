"""Mesa de siniestros (HU-W04): arma el expediente que consume el BFF.

Capas simples (regla #3): el servicio aplica las reglas y el repositorio guarda. La forma de salida
es la del contrato (`contracts/graphql/schema.graphql`, v0.21) en snake_case; el BFF solo resuelve
la póliza con POLICY y la URL firmada de cada evidencia con el almacenamiento.

La `resolucion` del contrato sale de dos colecciones: la aprobación o el rechazo
(`resoluciones_siniestro`) si existe; si no, la derivación a perito (`asignaciones_perito`), que
deja el caso EN_REVISION.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.application.bus_eventos import (
    PuertoBusEventos,
    evento_aprobado,
    evento_derivado,
    evento_rechazado,
)
from app.application.clientes import ServicioNoDisponible, SolicitudRechazada
from app.application.dias_habiles import sumar_dias_habiles
from app.application.identificadores import id_opaco

logger = logging.getLogger(__name__)

MAX_TEXTO = 500
ABIERTOS = ("PENDIENTE", "EN_REVISION")
# Quien aprueba un caso cuyo plazo venció sin gestión (D-3 de la revisión QA).
ACTOR_SISTEMA = "SISTEMA"
MOTIVOS = (
    "EVIDENCIA_INSUFICIENTE",
    "FUERA_DE_COBERTURA",
    "POLIZA_NO_VIGENTE",
    "INCONSISTENCIA_EN_RELATO",
    "OTRO",
)

# Mensajes de negocio en lenguaje del operador: los mismos del dummy, que la web ya muestra.
MENSAJE_NO_EXISTE = "No encontramos el siniestro."
MENSAJE_YA_RESUELTO = "El siniestro ya fue resuelto y no admite cambios."
MENSAJE_YA_DERIVADO = "El siniestro ya tiene un perito asignado."
MENSAJE_PERITO = "El perito seleccionado no está disponible."
MENSAJE_TEXTO_LARGO = f"El texto supera los {MAX_TEXTO} caracteres."
MENSAJE_MOTIVO = "El motivo de rechazo no es válido."
MENSAJE_POLIZA = "No pudimos consultar la póliza del siniestro. Intenta de nuevo."
MENSAJE_ORDEN_PAGO = "No pudimos registrar la orden de pago de la indemnización. Intenta de nuevo."

METODO_TRANSFERENCIA = "ACH Colombia Interbancario"
PLAZO_DISPERSION = "1 a 3 días hábiles"
NOTIFICACION_APROBACION = "Enviada al asegurado y a la entidad acreedora"
NOTIFICACION_RECHAZO = "Enviada vía correo certificado y Open Finance"
NOTIFICACION_DERIVACION = "Correo certificado y expediente digital de siniestros"
VIA_RECURSO = (
    "Disponible en los próximos 10 días hábiles conforme al Código de Comercio y Circular "
    "Básica Jurídica de la SFC."
)


class ErrorNegocio(Exception):
    """Regla de negocio incumplida: el mensaje es seguro para mostrar al operador."""


class DependenciaNoDisponible(Exception):
    """POLICY o PAYMENTS no respondieron: el caso sigue abierto y el operador puede reintentar."""


class PuertoPolicy(Protocol):
    def obtener_poliza(self, poliza_id: str) -> dict[str, Any]: ...


class PuertoPayments(Protocol):
    def ordenar_indemnizacion(
        self, siniestro_id: str, poliza_id: str, monto: str, moneda: str
    ) -> dict[str, Any]: ...


class PuertoRepositorio(Protocol):
    def listar(self, estado: str | None = None) -> list[dict]: ...

    def pendientes_vencidos(self, ahora: datetime) -> list[str]: ...

    def obtener(self, siniestro_id: str, primaria: bool = False) -> dict | None: ...

    def evidencias(self, ids: list[str], primaria: bool = False) -> dict[str, list[dict]]: ...

    def verificaciones(self, ids: list[str], primaria: bool = False) -> dict[str, list[dict]]: ...

    def asignaciones(self, ids: list[str], primaria: bool = False) -> dict[str, dict]: ...

    def resoluciones(self, ids: list[str], primaria: bool = False) -> dict[str, dict]: ...

    def peritos(self, primaria: bool = False) -> list[dict]: ...

    def perito(self, perito_id: str, primaria: bool = False) -> dict | None: ...

    def registrar_derivacion(
        self, siniestro_id: str, asignacion: dict, ahora: datetime
    ) -> bool: ...

    def registrar_resolucion(
        self, siniestro_id: str, resolucion: dict, estado: str, ahora: datetime
    ) -> bool: ...


def _perito(perito: dict | None) -> dict | None:
    if perito is None:
        return None
    return {
        "id": perito["id"],
        "nombre": perito["nombre"],
        "ciudad": perito["ciudad"],
        "red": perito["red"],
        "registro": perito["registro"],
        "tiempo_respuesta_dias": perito["tiempo_respuesta_dias"],
    }


def _evidencia(evidencia: dict) -> dict:
    return {
        "id": evidencia["id"],
        "archivo_id": evidencia["archivo_id"],
        "tipo": evidencia["tipo"],
        "titulo": evidencia["titulo"],
        "archivo": evidencia["nombre_archivo"],
        "registrada_en": evidencia["registrada_en"],
        "geoetiquetada": evidencia["geoetiquetada"],
        "latitud": evidencia.get("latitud"),
        "longitud": evidencia.get("longitud"),
        "nota": evidencia.get("nota"),
    }


class ServicioSiniestros:
    def __init__(
        self,
        repositorio: PuertoRepositorio,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        policy: PuertoPolicy | None = None,
        payments: PuertoPayments | None = None,
        bus: PuertoBusEventos | None = None,
    ) -> None:
        self._repo = repositorio
        self._reloj = reloj
        self._policy = policy
        self._payments = payments
        self._bus = bus

    # ---------- Lectura ----------

    def listar(self, estado: str | None = None) -> list[dict]:
        return self._armar(self._repo.listar(estado), primaria=False)

    def obtener(self, siniestro_id: str, primaria: bool = False) -> dict | None:
        siniestro = self._repo.obtener(siniestro_id, primaria=primaria)
        if siniestro is None:
            return None
        return self._armar([siniestro], primaria=primaria)[0]

    def peritos_para(self, siniestro_id: str) -> list[dict]:
        """Peritos disponibles, con la fecha límite del informe si se le deriva hoy. Caso
        inexistente: lista vacía."""
        if self._repo.obtener(siniestro_id) is None:
            return []
        ahora = self._reloj()
        return [
            {
                **_perito(p),
                "vencimiento_informe": sumar_dias_habiles(ahora, p["tiempo_respuesta_dias"]),
            }
            for p in self._repo.peritos()
        ]

    # ---------- Resolución (KAN-244) ----------
    # Cada acción devuelve el expediente leído de la primaria: el operador ve lo que acaba de hacer
    # aunque la réplica vaya atrasada.

    def _abierto(self, siniestro_id: str) -> dict:
        siniestro = self._repo.obtener(siniestro_id, primaria=True)
        if siniestro is None:
            raise ErrorNegocio(MENSAJE_NO_EXISTE)
        if siniestro["estado"] not in ABIERTOS:
            raise ErrorNegocio(MENSAJE_YA_RESUELTO)
        return siniestro

    @staticmethod
    def _texto(texto: str | None) -> str | None:
        texto = (texto or "").strip() or None
        if texto and len(texto) > MAX_TEXTO:
            raise ErrorNegocio(MENSAJE_TEXTO_LARGO)
        return texto

    def derivar(
        self, siniestro_id: str, perito_id: str, instrucciones: str | None, actor: str | None
    ) -> dict:
        siniestro = self._abierto(siniestro_id)
        if self._repo.asignaciones([siniestro_id], primaria=True):
            raise ErrorNegocio(MENSAJE_YA_DERIVADO)
        perito = self._repo.perito(perito_id, primaria=True)
        if perito is None or not perito["activo"]:
            raise ErrorNegocio(MENSAJE_PERITO)
        instrucciones = self._texto(instrucciones)
        ahora = self._reloj()
        asignacion = {
            "id": str(uuid.uuid4()),
            "siniestro_id": siniestro_id,
            "perito_id": perito_id,
            "instrucciones": instrucciones,
            "notificacion": NOTIFICACION_DERIVACION,
            "asignada_en": ahora,
            "vence_informe_en": sumar_dias_habiles(ahora, perito["tiempo_respuesta_dias"]),
            "actor_usuario_id": actor,
        }
        if not self._repo.registrar_derivacion(siniestro_id, asignacion, ahora):
            # Otro operador lo derivó o lo resolvió entre la validación y la escritura.
            raise ErrorNegocio(self._motivo_conflicto(siniestro_id, MENSAJE_YA_DERIVADO))
        logger.info("siniestro=%s derivado perito=%s", id_opaco(siniestro_id), perito_id)
        self._publicar(evento_derivado(siniestro, asignacion, actor))
        return self.obtener(siniestro_id, primaria=True)

    def rechazar(
        self, siniestro_id: str, motivo: str, comentario: str | None, actor: str | None
    ) -> dict:
        siniestro = self._abierto(siniestro_id)
        if motivo not in MOTIVOS:
            raise ErrorNegocio(MENSAJE_MOTIVO)
        comentario = self._texto(comentario)
        ahora = self._reloj()
        resolucion = {
            "id": str(uuid.uuid4()),
            "siniestro_id": siniestro_id,
            "tipo": "RECHAZO",
            "motivo": motivo,
            "comentario": comentario,
            "notificacion": NOTIFICACION_RECHAZO,
            "via_recurso": VIA_RECURSO,
            "resuelta_en": ahora,
            "actor_usuario_id": actor,
        }
        if not self._repo.registrar_resolucion(siniestro_id, resolucion, "RECHAZADO", ahora):
            raise ErrorNegocio(MENSAJE_YA_RESUELTO)
        logger.info("siniestro=%s rechazado motivo=%s", id_opaco(siniestro_id), motivo)
        self._publicar(evento_rechazado(siniestro, resolucion, actor))
        return self.obtener(siniestro_id, primaria=True)

    def aprobar_vencidos(self) -> dict[str, int]:
        """Una pasada del plazo del dictamen (D-3): cada caso PENDIENTE vencido se aprueba por el
        mismo camino que usa el operador, sin mirar las verificaciones (D-3a). Un caso derivado está
        EN_REVISION y espera al perito (D-3b). Un caso que falla sigue abierto para la próxima
        pasada, e idempotente: PAYMENTS devuelve la misma orden."""
        aprobados = pendientes = 0
        for siniestro_id in self._repo.pendientes_vencidos(self._reloj()):
            try:
                self.aprobar(siniestro_id, ACTOR_SISTEMA, automatica=True)
                aprobados += 1
            except (DependenciaNoDisponible, ErrorNegocio) as error:
                pendientes += 1
                logger.warning(
                    "siniestro=%s sin aprobar por vencimiento: %s", id_opaco(siniestro_id), error
                )
        return {"aprobados": aprobados, "pendientes": pendientes}

    def aprobar(self, siniestro_id: str, actor: str | None, automatica: bool = False) -> dict:
        """W04-6: primero la orden de pago en PAYMENTS (idempotente por siniestro) y después la
        aprobación. Si PAYMENTS o POLICY no responden, el caso sigue abierto y nada queda a medias:
        reintentar devuelve la misma orden."""
        siniestro = self._abierto(siniestro_id)
        indemnizacion = self._indemnizacion(siniestro)
        try:
            orden = self._payments.ordenar_indemnizacion(
                siniestro_id,
                siniestro["poliza_id"],
                indemnizacion["valor"],
                indemnizacion["moneda"],
            )
        except (ServicioNoDisponible, SolicitudRechazada) as error:
            logger.warning(
                "siniestro=%s orden de pago no registrada: %s", id_opaco(siniestro_id), error
            )
            raise DependenciaNoDisponible(MENSAJE_ORDEN_PAGO) from error
        ahora = self._reloj()
        resolucion = {
            "id": str(uuid.uuid4()),
            "siniestro_id": siniestro_id,
            "tipo": "APROBACION",
            "notificacion": NOTIFICACION_APROBACION,
            "indemnizacion": indemnizacion,
            "metodo_transferencia": orden.get("metodo_transferencia", METODO_TRANSFERENCIA),
            "plazo_dispersion": PLAZO_DISPERSION,
            "orden_pago_id": orden["id"],
            "automatica": automatica,
            "resuelta_en": ahora,
            "actor_usuario_id": actor,
        }
        if not self._repo.registrar_resolucion(siniestro_id, resolucion, "APROBADO", ahora):
            raise ErrorNegocio(MENSAJE_YA_RESUELTO)
        logger.info("siniestro=%s aprobado orden=%s", id_opaco(siniestro_id), id_opaco(orden["id"]))
        self._publicar(evento_aprobado(siniestro, resolucion, actor))
        return self.obtener(siniestro_id, primaria=True)

    def _publicar(self, evento: dict) -> None:
        """Después de guardar: si el bus falla o el evento no cumple su esquema, la resolución ya
        quedó guardada y no se revierte (W04-9)."""
        if self._bus is not None:
            self._bus.publicar(evento)

    def _indemnizacion(self, siniestro: dict) -> dict:
        """W04-8: la suma reclamada, acotada a la cobertura de la póliza (en la misma moneda)."""
        try:
            poliza = self._policy.obtener_poliza(siniestro["poliza_id"])
        except (ServicioNoDisponible, SolicitudRechazada) as error:
            raise DependenciaNoDisponible(MENSAJE_POLIZA) from error
        reclamada = siniestro["suma_reclamada"]
        cobertura = poliza["cobertura"]
        if cobertura["moneda"] == reclamada["moneda"] and Decimal(cobertura["valor"]) < Decimal(
            reclamada["valor"]
        ):
            return {"valor": cobertura["valor"], "moneda": cobertura["moneda"]}
        return {"valor": reclamada["valor"], "moneda": reclamada["moneda"]}

    def _motivo_conflicto(self, siniestro_id: str, por_defecto: str) -> str:
        actual = self._repo.obtener(siniestro_id, primaria=True)
        if actual is not None and actual["estado"] not in ABIERTOS:
            return MENSAJE_YA_RESUELTO
        return por_defecto

    def _armar(self, siniestros: list[dict], primaria: bool) -> list[dict]:
        ids = [s["id"] for s in siniestros]
        if not ids:
            return []
        evidencias = self._repo.evidencias(ids, primaria=primaria)
        verificaciones = self._repo.verificaciones(ids, primaria=primaria)
        asignaciones = self._repo.asignaciones(ids, primaria=primaria)
        resoluciones = self._repo.resoluciones(ids, primaria=primaria)
        peritos = {p["id"]: p for p in self._repo.peritos(primaria=primaria)}
        expedientes = []
        for s in siniestros:
            sid = s["id"]
            asignacion = asignaciones.get(sid)
            perito = None
            if asignacion is not None:
                perito = peritos.get(asignacion["perito_id"]) or self._repo.perito(
                    asignacion["perito_id"], primaria=primaria
                )
            expedientes.append(
                {
                    **{k: v for k, v in s.items()},
                    "evidencias": [_evidencia(e) for e in evidencias.get(sid, [])],
                    "verificaciones": [
                        {"descripcion": v["descripcion"], "cumple": v["cumple"]}
                        for v in verificaciones.get(sid, [])
                    ],
                    "resolucion": self._resolucion(resoluciones.get(sid), asignacion, perito),
                }
            )
        return expedientes

    @staticmethod
    def _resolucion(
        resolucion: dict | None, asignacion: dict | None, perito: dict | None
    ) -> dict | None:
        if resolucion is not None:
            return {
                "tipo": resolucion["tipo"],
                "resuelta_en": resolucion["resuelta_en"],
                "notificacion": resolucion["notificacion"],
                "motivo": resolucion.get("motivo"),
                "comentario": resolucion.get("comentario"),
                "via_recurso": resolucion.get("via_recurso"),
                "indemnizacion": resolucion.get("indemnizacion"),
                "metodo_transferencia": resolucion.get("metodo_transferencia"),
                "plazo_dispersion": resolucion.get("plazo_dispersion"),
                "orden_pago_id": resolucion.get("orden_pago_id"),
                "automatica": resolucion.get("automatica", False),
                # Si el caso pasó por un perito, la resolución final lo conserva (como el dummy).
                "perito": _perito(perito),
                "instrucciones": None,
                "vencimiento_informe": None,
            }
        if asignacion is not None:
            return {
                "tipo": "DERIVACION",
                "resuelta_en": asignacion["asignada_en"],
                "notificacion": asignacion["notificacion"],
                "motivo": None,
                "comentario": None,
                "via_recurso": None,
                "indemnizacion": None,
                "metodo_transferencia": None,
                "plazo_dispersion": None,
                "orden_pago_id": None,
                "automatica": False,
                "perito": _perito(perito),
                "instrucciones": asignacion.get("instrucciones"),
                "vencimiento_informe": asignacion["vence_informe_en"],
            }
        return None
