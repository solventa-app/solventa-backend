"""A quién se avisa al revocar un consentimiento (W01-A2), igual por GraphQL y por la API de socios.

Los avisos van en paralelo y ninguno hace fallar la revocación, que AUTH ya registró: si uno se
pierde, lo recoge la purga diaria de quien corresponda. Solo se loguea el id opaco."""

import asyncio
import logging

from app.identificadores import id_opaco
from app.servicio_firma_pago import ServicioFirmaPago

logger = logging.getLogger("bff.revocacion")


async def avisar_revocacion(
    clientes, firma_pago: ServicioFirmaPago, titular_id: str, consentimiento_id: str
) -> None:
    resultados = await asyncio.gather(
        clientes.acl.invalidar_cache_cliente(titular_id),
        firma_pago.consentimiento_revocado(titular_id, consentimiento_id),
        clientes.risk.consentimiento_revocado(titular_id, consentimiento_id),
        return_exceptions=True,
    )
    for destino, resultado in zip(("ACL", "POLICY", "RISK"), resultados, strict=True):
        if isinstance(resultado, Exception):
            logger.warning(
                "%s no recibió la revocación consentimiento=%s",
                destino,
                id_opaco(consentimiento_id),
            )
