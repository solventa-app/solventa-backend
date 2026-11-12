"""Identificador opaco para trazabilidad sin filtrar datos personales
(regla de arquitectura #6): nunca se loguea `cliente_id` en crudo.

Copiado de `services/acl-worker/app/application/identificadores.py` (mismo
hash corto y determinista): cada servicio es una imagen Docker independiente
con su propio contexto de build, así que no hay un paquete Python compartido
entre servicios — se duplica este helper de 3 líneas en vez de inventar un
paquete común solo para esto.
"""

import hashlib


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs — no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]
