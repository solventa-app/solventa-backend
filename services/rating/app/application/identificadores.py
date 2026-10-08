"""Identificadores opacos para trazabilidad sin filtrar datos personales
(regla de arquitectura #6): nunca se loguea `cliente_id` en crudo.

Mismo patrón que `services/acl-worker/app/application/identificadores.py`
(duplicado deliberadamente: cada servicio es independiente, sin código
compartido entre servicios)."""

import hashlib


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs — no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]
