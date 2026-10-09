"""Identificadores del BFF sin filtrar datos personales (regla de arquitectura #6).

`id_opaco` sigue el patrón de `services/risk/app/application/identificadores.py`
(duplicado deliberadamente: sin código compartido entre servicios)."""

import hashlib
import hmac


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs — no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]


def cliente_id_desde_documento(documento_identidad: str, clave: str) -> str:
    """`cliente_id` estable y opaco que ACL, RISK y RATING usan como llave.

    REWIRE (T-W01-4 / JWT del Sprint 2): cuando AUTH tenga la tabla de
    identidad, el `cliente_id` lo resolverá AUTH (a partir del usuario
    autenticado o del documento) y esta función desaparece junto con
    `CLIENTE_ID_CLAVE`. El único llamador es `ServicioOferta.solicitar`
    (`app/servicio_oferta.py`): ahí se reemplaza por la llamada a AUTH.

    HMAC con clave y no un SHA-256 plano: los documentos de identidad son un
    espacio pequeño y un hash sin clave se revierte por fuerza bruta."""
    normalizado = documento_identidad.strip().upper()
    return hmac.new(clave.encode("utf-8"), normalizado.encode("utf-8"), hashlib.sha256).hexdigest()
