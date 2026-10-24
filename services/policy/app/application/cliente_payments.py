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


class PuertoPayments(Protocol):
    def crear_cobro(self, orden: OrdenPrimerCobro) -> ResultadoCobro: ...


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

    def cerrar(self) -> None:
        self._cliente.close()
