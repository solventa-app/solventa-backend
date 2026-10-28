"""Cliente HTTP hacia PAYMENTS (`POST {PAYMENTS_URL}/cobros`).

El primer cobro de la prima se pide a PAYMENTS (decisión W03-4); POLICY nunca llama a la pasarela
(regla de arquitectura #2: eso lo hace PAYMENTS a través del ACL Worker). `POST /cobros` es
idempotente por `id_intento_cobro`: repetir la MISMA orden devuelve el cobro que ya existe sin
cobrar de nuevo, y es así como POLICY vuelve a consultar un cobro `pendiente`.

Si PAYMENTS no responde se lanza `PaymentsNoDisponible`: la emisión se reintenta y NO se da el cobro
por hecho ni por fallido."""

from dataclasses import dataclass
from typing import Protocol

import httpx


class PaymentsNoDisponible(Exception):
    """PAYMENTS no respondió (red, timeout, 5xx o una respuesta que no se entiende)."""


class CobroEnConflicto(Exception):
    """PAYMENTS ya tiene ese intento con otros datos (409): no debería pasar con una orden fija."""


@dataclass
class OrdenPrimerCobro:
    id_intento_cobro: str  # determinista desde la propuesta: reintentar no cobra dos veces
    poliza_id: str  # PAYMENTS lo llama `poliza_id`; aquí es el id de la propuesta
    cliente_id: str | None
    monto: str  # decimal como texto, nunca float
    moneda: str
    token_medio_pago: str  # opaco, del convenio de recaudo (W03-5); nunca datos de tarjeta


@dataclass
class ResultadoCobro:
    estado: str  # cobrando | cobrado | rechazado | pendiente
    cobro_id: str | None = None
    motivo: str | None = None


@dataclass
class SesionPago:
    """La sesión de la página de pago de la pasarela (HU-W03, W03-28), vista desde PAYMENTS.
    `estado`: abierta, cobrada, rechazada, fallida, vencida o no_disponible. `cobro` solo con
    `cobrada`."""

    estado: str
    sesion_id: str | None = None
    url_pago: str | None = None
    vence_en: str | None = None
    motivo: str | None = None
    fallos_seguidos: int = 0
    cobro: dict | None = None


class SesionRechazada(Exception):
    """La pasarela no aceptó la solicitud de sesión (p. ej. el retorno). Error de configuración."""


class PuertoPayments(Protocol):
    def crear_cobro(self, orden: OrdenPrimerCobro) -> ResultadoCobro: ...


class PuertoSesionesPago(Protocol):
    def abrir_sesion(
        self,
        propuesta_id: str,
        cliente_id: str | None,
        monto: str,
        moneda: str,
        descripcion: str,
        url_retorno: str,
        idioma: str,
    ) -> SesionPago: ...

    def confirmar_sesion(self, sesion_id: str, propuesta_id: str) -> SesionPago | None: ...


def _sesion(cuerpo: dict) -> SesionPago:
    return SesionPago(
        estado=cuerpo["estado"],
        sesion_id=cuerpo.get("sesion_id"),
        url_pago=cuerpo.get("url_pago"),
        vence_en=cuerpo.get("vence_en"),
        motivo=cuerpo.get("motivo"),
        fallos_seguidos=cuerpo.get("fallos_seguidos") or 0,
        cobro=cuerpo.get("cobro"),
    )


class ClientePayments:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def crear_cobro(self, orden: OrdenPrimerCobro) -> ResultadoCobro:
        try:
            respuesta = self._cliente.post(
                "/cobros",
                json={
                    "id_intento_cobro": orden.id_intento_cobro,
                    "poliza_id": orden.poliza_id,
                    "cliente_id": orden.cliente_id,
                    "monto": orden.monto,
                    "moneda": orden.moneda,
                    "token_medio_pago": orden.token_medio_pago,
                },
            )
            if respuesta.status_code == 409:
                raise CobroEnConflicto(orden.id_intento_cobro)
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            return ResultadoCobro(cuerpo["estado"], cuerpo.get("id"), cuerpo.get("motivo"))
        except CobroEnConflicto:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise PaymentsNoDisponible(type(error).__name__) from error

    def abrir_sesion(
        self,
        propuesta_id: str,
        cliente_id: str | None,
        monto: str,
        moneda: str,
        descripcion: str,
        url_retorno: str,
        idioma: str,
    ) -> SesionPago:
        """`POST /sesiones-pago`. PAYMENTS concilia la sesión anterior antes de abrir otra, así que
        repetirlo nunca cobra dos veces. Lanza `PaymentsNoDisponible` si PAYMENTS no responde."""
        try:
            respuesta = self._cliente.post(
                "/sesiones-pago",
                json={
                    "propuesta_id": propuesta_id,
                    "cliente_id": cliente_id,
                    "monto": monto,
                    "moneda": moneda,
                    "descripcion": descripcion,
                    "url_retorno": url_retorno,
                    "idioma": idioma,
                },
            )
            if respuesta.status_code == 422:
                raise SesionRechazada(str(respuesta.json().get("detail")))
            respuesta.raise_for_status()
            return _sesion(respuesta.json())
        except SesionRechazada:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise PaymentsNoDisponible(type(error).__name__) from error

    def confirmar_sesion(self, sesion_id: str, propuesta_id: str) -> SesionPago | None:
        """`POST /sesiones-pago/{id}/confirmar`. `None` si la sesión no es de la propuesta."""
        try:
            respuesta = self._cliente.post(
                f"/sesiones-pago/{sesion_id}/confirmar", json={"propuesta_id": propuesta_id}
            )
            if respuesta.status_code == 404:
                return None
            respuesta.raise_for_status()
            return _sesion(respuesta.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise PaymentsNoDisponible(type(error).__name__) from error

    def cerrar(self) -> None:
        self._cliente.close()
