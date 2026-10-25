"""Cliente HTTP hacia UNDER (`POST {UNDER_URL}/decisiones`).

UNDER decide la suscripción (KYC por el ACL Worker y reglas). Si no responde, **no se decide por
él**: se lanza `UnderNoDisponible` y la emisión se reintenta (la propuesta sigue `EN_EMISION`). Una
decisión de revisión manual o de rechazo es una respuesta de negocio, no un error."""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class UnderNoDisponible(Exception):
    """UNDER no respondió (red, timeout, 5xx o una respuesta que no se entiende)."""


@dataclass
class ResultadoDecision:
    decision: str  # APROBADA | RECHAZADA | REVISION_MANUAL
    motivos: list[str] = field(default_factory=list)


class PuertoUnder(Protocol):
    def decidir(
        self,
        propuesta_id: str,
        cliente_id: str,
        nivel_riesgo: str | None,
        cobertura: dict[str, Any],
    ) -> ResultadoDecision: ...


class ClienteUnder:
    def __init__(self, url: str, timeout_s: float, auth: httpx.Auth | None = None) -> None:
        self._cliente = httpx.Client(base_url=url, timeout=timeout_s, auth=auth)

    def decidir(
        self,
        propuesta_id: str,
        cliente_id: str,
        nivel_riesgo: str | None,
        cobertura: dict[str, Any],
    ) -> ResultadoDecision:
        try:
            respuesta = self._cliente.post(
                "/decisiones",
                json={
                    "propuesta_id": propuesta_id,
                    "cliente_id": cliente_id,
                    "nivel_riesgo": nivel_riesgo,
                    "cobertura": {"valor": cobertura["valor"], "moneda": cobertura["moneda"]},
                },
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            return ResultadoDecision(cuerpo["decision"], list(cuerpo.get("motivos", [])))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise UnderNoDisponible(type(error).__name__) from error

    def cerrar(self) -> None:
        self._cliente.close()
