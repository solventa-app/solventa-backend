"""Caso de uso: pagar en la página alojada de la pasarela (HU-W03, W03-28 a W03-30; W05-A1 a A4).

El titular paga en la página de la pasarela, no en Solventa: PAYMENTS abre la sesión (por el ACL,
regla 2) y, cuando el navegador regresa, **confirma el resultado de servidor a servidor**, sin
confiar en la redirección.

    abrir ─► ¿hay una sesión anterior abierta? ─► se concilia primero con la pasarela
               ├─ ya se cobró ─────────────► se devuelve cobrada (nunca se cobra dos veces)
               ├─ sigue abierta y vigente ─► se devuelve la misma (misma URL)
               └─ vencida, rechazada o fallida ─► sesión nueva, intento n + 1
    confirmar ─► la pasarela dice:
               ├─ cobrada ─► medio de pago + cobro INTERACTIVO + sesión cobrada (una transacción)
               ├─ rechazada ─► el titular elige otra tarjeta (sesión nueva, contador en 0)
               ├─ fallida o sin respuesta ─► una falla más; al 3.er fallo seguido, otra tarjeta
               └─ vencida ─► se abre otra

Cada sesión es un intento nuevo: `id_intento_cobro = uuid5(propuesta, n)` (una tarjeta nueva o una
sesión vencida nunca reutilizan la referencia de otra). Nunca un 5xx por la pasarela (regla 4): sin
respuesta, la sesión queda como estaba y el motivo dice qué hacer. Ningún dato de tarjeta llega
aquí: solo el token, la marca y el tipo."""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.application.bus_eventos import PuertoBusEventos, construir_evento_cobro_estado_cambiado
from app.application.cliente_acl import PuertoClienteAcl, SesionPasarela, SolicitudSesion
from app.application.identificadores import id_opaco
from app.application.repositorio_sesiones import RepositorioSesiones, vence_en

logger = logging.getLogger("payments.sesiones")

# W03-30: al tercer fallo seguido de la pasarela se pide otra tarjeta.
FALLOS_PARA_OTRA_TARJETA = 3
ESPACIO_INTENTOS = uuid.UUID("6f1c5e4a-2b7d-4c1e-9a3f-5d8b0e2c7a14")

MOTIVO_NO_DISPONIBLE = "pasarela_no_disponible"
MOTIVO_OTRA_TARJETA = "requiere_otra_tarjeta"
MOTIVO_NO_ENCONTRADA = "sesion_no_encontrada"


class SolicitudSesionInvalida(Exception):
    """La pasarela no aceptó la solicitud (p. ej. un retorno fuera de su lista blanca). Es un error
    de configuración de Solventa, no una falla de la pasarela: no cuenta como fallo."""


def id_intento_sesion(propuesta_id: str, intento: int) -> str:
    return str(uuid.uuid5(ESPACIO_INTENTOS, f"{propuesta_id}:{intento}"))


class ServicioSesionesPago:
    def __init__(
        self,
        sesiones: RepositorioSesiones,
        cliente_acl: PuertoClienteAcl,
        bus: PuertoBusEventos,
        obtener_cobro: Callable[[str], dict | None],
        fallos_para_otra_tarjeta: int = FALLOS_PARA_OTRA_TARJETA,
    ) -> None:
        self._sesiones = sesiones
        self._acl = cliente_acl
        self._bus = bus
        self._obtener_cobro = obtener_cobro
        self._fallos_maximos = fallos_para_otra_tarjeta

    # ------------------------------------------------------------------ abrir

    def abrir(self, solicitud: dict[str, Any]) -> dict[str, Any]:
        """Abre la sesión de pago de la propuesta, o devuelve la vigente. Repetirlo no abre otra
        mientras la anterior siga abierta, y nunca abre otra si la anterior ya se cobró."""
        propuesta_id = solicitud["propuesta_id"]
        ultima = self._sesiones.ultima(propuesta_id)
        if ultima is not None and ultima["estado"] == "abierta":
            ultima, cobro, sin_respuesta = self._conciliar(ultima)
            if ultima["estado"] in ("abierta", "cobrada") or sin_respuesta:
                # Sin respuesta no se abre otra: la anterior pudo haberse pagado.
                return self._respuesta(ultima, cobro)
        elif ultima is not None and ultima["estado"] == "cobrada":
            return self._respuesta(ultima)

        intento = (ultima["intento"] + 1) if ultima else 1
        heredados = ultima["fallos_seguidos"] if ultima else 0
        fallos = heredados if heredados < self._fallos_maximos else 0
        id_intento = id_intento_sesion(propuesta_id, intento)
        resultado = self._acl.crear_sesion(
            SolicitudSesion(
                id_intento_cobro=id_intento,
                monto=solicitud["monto"],
                moneda=solicitud["moneda"],
                descripcion=solicitud["descripcion"],
                url_retorno=solicitud["url_retorno"],
                idioma=solicitud.get("idioma", "es"),
            )
        )
        fila = {
            "id": str(uuid.uuid4()),
            "propuesta_id": propuesta_id,
            "cliente_id": solicitud.get("cliente_id"),
            "intento": intento,
            "id_intento_cobro": id_intento,
            "monto": solicitud["monto"],
            "moneda": solicitud["moneda"],
            "proveedor_sesion_id": None,
            "url_pago": None,
            "vence_en": None,
        }
        sesion = resultado.sesion
        if resultado.resultado == "creada" and sesion is not None:
            if sesion.estado == "rechazada":
                logger.warning(
                    "la pasarela rechazó la solicitud de sesión propuesta=%s motivo=%s",
                    id_opaco(propuesta_id),
                    sesion.motivo,
                )
                raise SolicitudSesionInvalida(sesion.motivo or "solicitud_invalida")
            fila.update(
                proveedor_sesion_id=sesion.sesion_id,
                url_pago=sesion.url_pago,
                vence_en=vence_en(sesion.vence_en),
                estado="abierta",
                motivo=None,
                fallos_seguidos=fallos,
            )
        else:
            fallos += 1
            fila.update(estado="no_disponible", motivo=self._motivo_falla(fallos))
            fila["fallos_seguidos"] = fallos
        creada = self._sesiones.crear(fila)
        logger.info(
            "sesión de pago propuesta=%s intento=%s estado=%s",
            id_opaco(propuesta_id),
            creada["intento"],
            creada["estado"],
        )
        return self._respuesta(creada)

    # ------------------------------------------------------------------ confirmar

    def confirmar(self, proveedor_sesion_id: str, propuesta_id: str) -> dict[str, Any] | None:
        """Al regresar de la pasarela. `None` si la sesión no existe o es de otra propuesta.
        Repetirlo es seguro: una sesión cobrada se devuelve con su cobro, sin cobrar de nuevo."""
        sesion = self._sesiones.por_proveedor(proveedor_sesion_id)
        if sesion is None or sesion["propuesta_id"] != propuesta_id:
            return None
        if sesion["estado"] != "abierta":
            return self._respuesta(sesion)
        sesion, cobro, _ = self._conciliar(sesion)
        return self._respuesta(sesion, cobro)

    def ultima(self, propuesta_id: str) -> dict[str, Any] | None:
        """La última sesión de la propuesta, sin preguntarle a la pasarela (para la reconciliación
        de POLICY, que luego confirma)."""
        sesion = self._sesiones.ultima(propuesta_id)
        return self._respuesta(sesion) if sesion else None

    # ------------------------------------------------------------------ internos

    def _motivo_falla(self, fallos: int) -> str:
        return MOTIVO_OTRA_TARJETA if fallos >= self._fallos_maximos else MOTIVO_NO_DISPONIBLE

    def _conciliar(self, sesion: dict) -> tuple[dict, dict | None, bool]:
        """Le pregunta a la pasarela por una sesión abierta y la pone al día. Devuelve la sesión,
        el cobro (si se cobró) y si la pasarela no respondió."""
        if not sesion.get("proveedor_sesion_id"):
            return sesion, None, False
        consulta = self._acl.consultar_sesion(sesion["proveedor_sesion_id"])
        if consulta.resultado == "desconocido":
            fallos = sesion["fallos_seguidos"] + 1
            actualizada = self._sesiones.actualizar(
                sesion["id"], "abierta", self._motivo_falla(fallos), fallos
            )
            logger.info(
                "pasarela sin respuesta al confirmar propuesta=%s fallos=%s",
                id_opaco(sesion["propuesta_id"]),
                fallos,
            )
            return actualizada, None, True
        if consulta.resultado == "no_encontrado" or consulta.sesion is None:
            # P. ej. la pasarela de prueba se reinició: para Solventa es una sesión que venció.
            return (
                self._sesiones.actualizar(
                    sesion["id"], "vencida", MOTIVO_NO_ENCONTRADA, sesion["fallos_seguidos"]
                ),
                None,
                False,
            )
        return self._aplicar(sesion, consulta.sesion)

    def _aplicar(self, sesion: dict, pasarela: SesionPasarela) -> tuple[dict, dict | None, bool]:
        if pasarela.estado == "cobrada" and pasarela.medio:
            actualizada, cobro = self._sesiones.registrar_pago(
                sesion, pasarela.medio, pasarela.referencia
            )
            logger.info(
                "pago confirmado propuesta=%s intento=%s",
                id_opaco(sesion["propuesta_id"]),
                sesion["intento"],
            )
            self._bus.publicar(
                construir_evento_cobro_estado_cambiado(
                    cobro_id=cobro["id"],
                    poliza_id=cobro["poliza_id"],
                    estado=cobro["estado"],
                    id_intento_cobro=cobro["id_intento_cobro"],
                )
            )
            return actualizada, cobro, False
        if pasarela.estado == "rechazada":
            # Un rechazo no es una falla de la pasarela: el titular elige otra tarjeta.
            return (
                self._sesiones.actualizar(sesion["id"], "rechazada", pasarela.motivo, 0),
                None,
                False,
            )
        if pasarela.estado == "fallida":
            fallos = sesion["fallos_seguidos"] + 1
            return (
                self._sesiones.actualizar(
                    sesion["id"], "fallida", self._motivo_falla(fallos), fallos
                ),
                None,
                False,
            )
        if pasarela.estado == "vencida":
            return (
                self._sesiones.actualizar(
                    sesion["id"], "vencida", "sesion_vencida", sesion["fallos_seguidos"]
                ),
                None,
                False,
            )
        # abierta o procesando: el titular volvió sin pagar o el pago sigue en curso.
        return sesion, None, False

    def _respuesta(self, sesion: dict, cobro: dict | None = None) -> dict[str, Any]:
        if cobro is None and sesion.get("cobro_id"):
            cobro = self._obtener_cobro(sesion["cobro_id"])
        respuesta = dict(sesion)
        respuesta["cobro"] = cobro
        if isinstance(respuesta.get("vence_en"), datetime):
            respuesta["vence_en"] = respuesta["vence_en"].astimezone(UTC)
        return respuesta
