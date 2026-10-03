"""
Puertos de dominio del ACL Worker (HA-MOD-001, arquitectura hexagonal).

El dominio y la capa de aplicación solo conocen estas interfaces: nunca saben si detrás hay un stub,
un sandbox o el proveedor real. Cambiar de proveedor es agregar un adaptador, no tocar el Circuit
Breaker ni los casos de uso (criterio de aceptación de HA-MOD-001 / EC031).

Los puertos son SÍNCRONOS, igual que en el Experimento 1: `purgatory` expone una API síncrona y es
la capa HTTP (FastAPI) quien los ejecuta en un hilo con `asyncio.to_thread`.

PROPUESTA inicial del Sprint 1: afinar las firmas en T-W01-8, T-W01-9 y T-W05-2 y registrar el
cambio en `docs/arquitectura-backend.md`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

# Las 5 fuentes de la pantalla W01-04: 2 de Open Finance y 3 de Open Data.
Fuente = str


@dataclass
class ConsultaFuente:
    cliente_id: str
    consentimiento_id: str  # sin consentimiento vigente no se consulta ninguna fuente (CA-W01-01)
    fuente: Fuente


@dataclass
class DatosFuente:
    fuente: Fuente
    datos: dict[str, Any]
    capturado_en: datetime
    de_cache: bool = False  # True si se degradó al último valor en caché (CA-W01-04, EC010)
    degradado: bool = False  # True si no hay dato real ni caché y se usa el valor por defecto


class PuertoFuenteFinanciera(Protocol):
    """Open Finance (circular 004 de 2024 de la SFC)."""

    def consultar(self, consulta: ConsultaFuente) -> DatosFuente: ...


class PuertoFuenteAbierta(Protocol):
    """Open Data."""

    def consultar(self, consulta: ConsultaFuente) -> DatosFuente: ...


EstadoCobro = Literal["cobrado", "rechazado", "pendiente"]


@dataclass
class OrdenCobro:
    clave_idempotencia: str  # un mismo intento recibido dos veces se ejecuta una vez (CA-W05-04)
    poliza_id: str
    monto: str  # decimal como texto, nunca float
    moneda: str
    token_medio_pago: str  # token del proveedor PCI-DSS: nunca datos de tarjeta (EC031)


@dataclass
class ResultadoCobro:
    estado: EstadoCobro
    referencia: str | None = None
    motivo: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


class PuertoPasarelaPagos(Protocol):
    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro: ...
