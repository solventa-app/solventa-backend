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


def cliente_id_desde_documento(
    documento: str, clave: str, pais: str | None = None, tipo: str | None = None
) -> str:
    """`titular_id` estable y opaco calculado con HMAC-SHA256 sobre el documento de identidad
    (mismo algoritmo que usa el BFF). AUTH nunca guarda ni loguea el documento en claro. T20: con
    país y tipo (ver `valor_titular`)."""
    return hmac.new(
        clave.encode("utf-8"), valor_titular(documento, pais, tipo).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def valor_titular(numero: str, pais: str | None = None, tipo: str | None = None) -> str:
    """T20: lo que entra al HMAC del `titular_id`. Cédula de ciudadanía colombiana (o sin país ni
    tipo, como antes de T20): solo el número, para que los titulares, consentimientos y pólizas
    que ya existen sigan ligados. Cualquier otro par: `PAIS:TIPO:NUMERO`, así dos documentos con
    el mismo número en tipos o países distintos no son la misma persona."""
    limpio = numero.strip().upper()
    pais_n = (pais or "CO").strip().upper()
    tipo_n = (tipo or "CC").strip().upper()
    if (pais_n, tipo_n) == ("CO", "CC"):
        return limpio
    return f"{pais_n}:{tipo_n}:{limpio}"
