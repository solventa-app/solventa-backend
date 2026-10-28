"""Quién escucha qué (HU-W03, W03-37). El ticket que emite el BFF trae el rol, el usuario y, para un
CLIENTE, su titular; de ahí salen los canales de Redis en los que POLICY publica (sin datos
personales):

- ASESOR: `avisos:asesor:<usuario>`, las propuestas que aceptó.
- CLIENTE: `avisos:titular:<titular>`, las de su titular verificado.
- BACKOFFICE: `avisos:todos` (propuestas) y `avisos:cobros` (cobros, W05-A6)."""

CANAL_COBROS = "avisos:cobros"
CANAL_TODOS = "avisos:todos"


def canales_de(ticket: dict) -> list[str]:
    """Los canales de quien presentó el ticket; vacío si es un rol desconocido o le falta el id."""
    rol, usuario, titular = ticket.get("rol"), ticket.get("usuarioId"), ticket.get("titularId")
    if rol == "ASESOR" and usuario:
        return [f"avisos:asesor:{usuario}"]
    if rol == "CLIENTE" and titular:  # sin identidad verificada no hay de qué enterarse
        return [f"avisos:titular:{titular}"]
    if rol == "BACKOFFICE":
        return [CANAL_TODOS, CANAL_COBROS]
    return []
