"""Consumo del ticket de un solo uso que emite el BFF (`app/ticket_avisos.py` del BFF)."""

import json

PREFIJO_TICKET = "avisos:ticket:"
LARGO_MAXIMO = 200


class TicketInvalido(Exception):
    """No existe, venció o ya se usó."""


async def consumir_ticket(redis, ticket: str) -> dict:
    """Lee y borra el ticket en un solo paso (`GETDEL`): dos pestañas con el mismo ticket no pueden
    abrir ambas. Lanza `TicketInvalido`; cualquier otra excepción es Redis caído."""
    if not ticket or len(ticket) > LARGO_MAXIMO:
        raise TicketInvalido
    crudo = await redis.getdel(PREFIJO_TICKET + ticket)
    if crudo is None:
        raise TicketInvalido
    try:
        datos = json.loads(crudo)
    except ValueError as error:
        raise TicketInvalido from error
    if not isinstance(datos, dict):
        raise TicketInvalido
    return datos
