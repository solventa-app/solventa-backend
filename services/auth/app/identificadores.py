"""Identificadores opacos para trazabilidad sin filtrar datos personales
(regla de arquitectura #6).

Mismo patrón que `services/risk/app/application/identificadores.py`
(duplicado deliberadamente: cada servicio es independiente, sin código
compartido entre servicios)."""

import hashlib
import hmac


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs — no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]


def cliente_id_desde_documento(documento: str, clave: str) -> str:
    """`titular_id` estable y opaco calculado con HMAC-SHA256 sobre el documento de identidad
    (mismo algoritmo que usa el BFF). AUTH nunca guarda ni loguea el documento en claro."""
    return hmac.new(
        clave.encode("utf-8"), documento.strip().encode("utf-8"), hashlib.sha256
    ).hexdigest()
