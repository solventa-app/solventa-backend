"""Identificadores opacos (regla de arquitectura #6) y el `titular_id`.

Duplicado deliberadamente de `services/auth/app/identificadores.py`: cada servicio es
independiente, sin código compartido entre servicios."""

import hashlib
import hmac
import re

_SEPARADORES = re.compile(r"[\s.\-]")


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs: no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]


def titular_id_desde_documento(
    numero: str, clave: str, pais: str | None = None, tipo: str | None = None
) -> str:
    """HMAC-SHA256 del número de documento, **mismo algoritmo y clave que AUTH y el BFF**
    (`CLIENTE_ID_CLAVE`): así el `titular_id` de M01 coincide con el de las pólizas de W03 y se
    pueden ligar. El número en claro nunca se guarda ni se loguea. T20: con país y tipo."""
    return hmac.new(
        clave.encode("utf-8"), valor_titular(numero, pais, tipo).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def valor_titular(numero: str, pais: str | None = None, tipo: str | None = None) -> str:
    """T20: lo que entra al HMAC del `titular_id`. Cédula de ciudadanía colombiana (o sin país ni
    tipo, como antes de T20): solo el número, para que los titulares, consentimientos y pólizas
    que ya existen sigan ligados. Cualquier otro par: `PAIS:TIPO:NUMERO`, así dos documentos con
    el mismo número en tipos o países distintos no son la misma persona."""
    # Sin puntos, espacios ni guiones: la cédula que lee la verificación de identidad
    # («1.090.000.001») y la que digita el asesor («1090000001») son el mismo titular, y su
    # póliza queda en la misma cuenta (QA 2026-11-10).
    limpio = _SEPARADORES.sub("", numero).upper()
    pais_n = (pais or "CO").strip().upper()
    tipo_n = (tipo or "CC").strip().upper()
    if (pais_n, tipo_n) == ("CO", "CC"):
        return limpio
    return f"{pais_n}:{tipo_n}:{limpio}"
