"""Cliente HTTP hacia el ACL Worker (`POST {ACL_URL}/pagos/cobrar`).

PAYMENTS **nunca** llama a la pasarela directamente (regla de arquitectura
#2): este es el único punto de contacto con `acl-worker`. Reusa tal cual el
shape de `OrdenCobro`/`ResultadoCobro` que ya expone
`services/acl-worker/app/domain/puertos.py` — no se inventa un contrato
nuevo entre PAYMENTS y el ACL Worker.

Si la llamada al ACL Worker falla por cualquier motivo (red, timeout, 5xx,
ACL Worker caído), nunca se propaga la excepción hacia arriba: se devuelve
`ResultadoCobro(estado="pendiente", motivo="acl_no_disponible")`, igual que
el ACL Worker hace con la pasarela (regla de arquitectura #4: nunca un 5xx
crudo al cliente). El cobro queda consultable y el reintento local
(`app/application/reintento.py`) lo vuelve a intentar."""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.application.identificadores import id_opaco

logger = logging.getLogger("payments.cliente_acl")


@dataclass
class OrdenCobro:
    id_intento_cobro: str
    poliza_id: str
    monto: str  # decimal como texto, nunca float
    moneda: str
    token_medio_pago: str


@dataclass
class ResultadoCobro:
    estado: str
    referencia: str | None = None
    motivo: str | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResultadoConsulta:
    """Conciliación (KAN-28): `encontrado` (con estado y referencia de la pasarela),
    `no_encontrado` o `desconocido` (no se pudo preguntar)."""

    resultado: str
    estado: str | None = None
    referencia: str | None = None
    motivo: str | None = None


class PuertoClienteAcl(Protocol):
    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro: ...

    def consultar(self, id_intento_cobro: str) -> ResultadoConsulta: ...


class ClienteAcl:
    def __init__(self, base_url: str, timeout_segundos: float) -> None:
        self._cliente = httpx.Client(base_url=base_url, timeout=timeout_segundos)

    def cobrar(self, orden: OrdenCobro) -> ResultadoCobro:
        try:
            respuesta = self._cliente.post(
                "/pagos/cobrar",
                json={
                    "id_intento_cobro": orden.id_intento_cobro,
                    "poliza_id": orden.poliza_id,
                    "monto": orden.monto,
                    "moneda": orden.moneda,
                    "token_medio_pago": orden.token_medio_pago,
                },
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            return ResultadoCobro(
                estado=cuerpo["estado"],
                referencia=cuerpo.get("referencia"),
                motivo=cuerpo.get("motivo"),
                detalle=cuerpo.get("detalle") or {},
            )
        except Exception as error:
            logger.warning(
                "fallo llamando al acl-worker poliza=%s error=%s",
                id_opaco(orden.poliza_id),
                type(error).__name__,
            )
            return ResultadoCobro(estado="pendiente", motivo="acl_no_disponible")

    def consultar(self, id_intento_cobro: str) -> ResultadoConsulta:
        """`GET {ACL_URL}/pagos/cobros/{clave}`: ¿la pasarela ya tiene este cobro?
        Ante cualquier falla devuelve `desconocido` (nunca propaga, como `cobrar`)."""
        try:
            respuesta = self._cliente.get(f"/pagos/cobros/{id_intento_cobro}")
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
            return ResultadoConsulta(
                resultado=cuerpo["resultado"],
                estado=cuerpo.get("estado"),
                referencia=cuerpo.get("referencia"),
                motivo=cuerpo.get("motivo"),
            )
        except Exception as error:
            logger.warning(
                "fallo consultando al acl-worker referencia=%s error=%s",
                id_opaco(id_intento_cobro),
                type(error).__name__,
            )
            return ResultadoConsulta(resultado="desconocido")

    def cerrar(self) -> None:
        self._cliente.close()
