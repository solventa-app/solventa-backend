"""Identificadores opacos para trazabilidad sin filtrar datos personales
(regla de arquitectura #6): nunca se loguea `cliente_id` en crudo."""

import hashlib


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs — no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]
