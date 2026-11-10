"""Identificadores del BFF sin filtrar datos personales (regla de arquitectura #6).

`id_opaco` sigue el patrón de `services/risk/app/application/identificadores.py`
(duplicado deliberadamente: sin código compartido entre servicios)."""

import hashlib
import hmac
import re


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs — no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]


def cliente_id_desde_documento(
    documento_identidad: str, clave: str, pais: str | None = None, tipo: str | None = None
) -> str:
    """`cliente_id` estable y opaco que ACL, RISK y RATING usan como llave.

    REWIRE (T-W01-4 / JWT del Sprint 2): cuando AUTH tenga la tabla de
    identidad, el `cliente_id` lo resolverá AUTH (a partir del usuario
    autenticado o del documento) y esta función desaparece junto con
    `CLIENTE_ID_CLAVE`. El único llamador es `ServicioOferta.solicitar`
    (`app/servicio_oferta.py`): ahí se reemplaza por la llamada a AUTH.

    HMAC con clave y no un SHA-256 plano: los documentos de identidad son un
    espacio pequeño y un hash sin clave se revierte por fuerza bruta. T20: con país y tipo (ver
    `valor_titular`; mismo algoritmo en AUTH y en onboarding)."""
    normalizado = valor_titular(documento_identidad, pais, tipo)
    return hmac.new(clave.encode("utf-8"), normalizado.encode("utf-8"), hashlib.sha256).hexdigest()


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


# ---------- KAN-290: placas ----------

# Placa colombiana: ABC123 (carros) o ABC12D (motos). Se escribe con o sin espacios y guiones.
_PLACA = re.compile(r"^[A-Z]{3}[0-9]{2}[A-Z0-9]$")
_SEPARADORES_PLACA = re.compile(r"[\s.\-]")


def normalizar_placa(placa: str) -> str | None:
    """La placa sin espacios ni guiones y en mayúsculas, o `None` si no tiene forma de placa."""
    limpia = _SEPARADORES_PLACA.sub("", placa or "").upper()
    return limpia if _PLACA.fullmatch(limpia) else None


def vehiculo_id_desde_placa(placa: str, clave: str) -> str:
    """El `vehiculo_id`: HMAC de la placa con la misma clave del titular y un prefijo propio, para
    que nunca coincida con el de un documento. Es la llave de la caché del ACL y del perfil del
    vehículo en RISK; la placa en claro solo viaja al proveedor (regla 6)."""
    return hmac.new(clave.encode("utf-8"), f"vehiculo:{placa}".encode(), hashlib.sha256).hexdigest()


def enmascarar_placa(placa: str) -> str:
    return f"{placa[:3]}•••"
