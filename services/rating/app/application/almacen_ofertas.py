"""Almacén de la oferta generada, para poder aceptarla después (HU-W03, decisión W03-1).

RATING no es dueño de la venta: guarda en Redis una copia de la oferta que acaba de calcular, con un
TTL igual a su vigencia, y la sirve en `GET /ofertas/{id}`. POLICY la lee de aquí al aceptarla y
**nunca confía en el precio que manda el cliente**. Si Redis la perdió o venció, la oferta "no
existe" y el cliente vuelve a cotizar.

A diferencia de la caché del score, esta copia no es recalculable (el `id` de la oferta es nuevo en
cada cálculo), pero un fallo de Redis se absorbe igual (regla de arquitectura #4): el peor caso es
que el cliente tenga que cotizar de nuevo, no un 5xx al generar la oferta.

La clave no lleva el cliente en claro: el valor lleva `clienteId`, que ya es el identificador opaco
(HMAC) que calcula el BFF, nunca un documento."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.cache_score import PuertoAlmacenClaveValor

PREFIJO_CLAVE = "oferta-aceptable:"


def _clave(oferta_id: str) -> str:
    return f"{PREFIJO_CLAVE}{oferta_id}"


class AlmacenOfertas:
    def __init__(self, almacen: PuertoAlmacenClaveValor | None, vigencia_segundos: int) -> None:
        self._almacen = almacen
        self._vigencia_segundos = vigencia_segundos

    def guardar(
        self,
        oferta: dict[str, Any],
        cliente_id: str,
        consentimiento_id: str | None,
        plazo_meses: int | None,
    ) -> None:
        """Guarda la oferta con lo que POLICY y el BFF necesitan para validarla al aceptar."""
        if self._almacen is None:
            return
        vigente_hasta = datetime.now(UTC) + timedelta(seconds=self._vigencia_segundos)
        valor = {
            **oferta,
            "clienteId": cliente_id,
            "consentimientoId": consentimiento_id,
            "plazoMeses": plazo_meses,
            "vigenteHasta": vigente_hasta.isoformat(),
        }
        try:
            self._almacen.set(_clave(oferta["id"]), json.dumps(valor), ex=self._vigencia_segundos)
        except Exception:
            pass  # cache-aside: no se rompe la oferta ya calculada por un fallo de Redis

    def obtener(self, oferta_id: str) -> dict[str, Any] | None:
        if self._almacen is None:
            return None
        try:
            crudo = self._almacen.get(_clave(oferta_id))
        except Exception:
            return None
        if crudo is None:
            return None
        return json.loads(crudo)
