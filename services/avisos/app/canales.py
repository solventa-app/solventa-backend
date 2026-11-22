"""Quién escucha qué (HU-W03, W03-37). El ticket que emite el BFF trae el rol, el usuario y, para un
CLIENTE, su titular; de ahí salen los canales de Redis en los que POLICY publica (sin datos
personales):

- ASESOR: `avisos:asesor:<usuario>`, las propuestas que aceptó.
- CLIENTE: `avisos:titular:<titular>`, las de su titular verificado.
- BACKOFFICE: `avisos:todos` (propuestas) y `avisos:cobros` (cobros, W05-A6).
- SOCIO: `avisos:socio:<socio>:<entorno>`, cada cotización, propuesta o póliza del socio en ese
  entorno (el panel se pone al día solo, contrato v0.68). Lo publican el BFF y POLICY."""

CANAL_COBROS = "avisos:cobros"
CANAL_TODOS = "avisos:todos"
ENTORNOS_SOCIO = ("PRODUCCION", "PRUEBAS")


def canal_socio(socio_id: str, entorno: str) -> str:
    return f"avisos:socio:{socio_id}:{entorno}"


def canales_de(ticket: dict) -> list[str]:
    """Los canales de quien presentó el ticket; vacío si es un rol desconocido o le falta el id."""
    rol, usuario, titular = ticket.get("rol"), ticket.get("usuarioId"), ticket.get("titularId")
    if rol == "ASESOR" and usuario:
        return [f"avisos:asesor:{usuario}"]
    if rol == "CLIENTE" and titular:  # sin identidad verificada no hay de qué enterarse
        return [f"avisos:titular:{titular}"]
    if rol == "SOCIO" and usuario and ticket.get("entorno") in ENTORNOS_SOCIO:
        return [canal_socio(usuario, ticket["entorno"])]
    if rol == "BACKOFFICE":
        return [CANAL_TODOS, CANAL_COBROS]
    return []
