"""Identificadores opacos (regla de arquitectura #6) y el `titular_id`.

Duplicado deliberadamente de `services/auth/app/identificadores.py`: cada servicio es
independiente, sin código compartido entre servicios."""

import hashlib
import hmac


def id_opaco(valor: str) -> str:
    """Hash corto y determinista, solo para correlacionar logs: no reversible."""
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()[:12]


def titular_id_desde_documento(numero: str, clave: str) -> str:
    """HMAC-SHA256 del número de documento, **mismo algoritmo y clave que AUTH y el BFF**
    (`CLIENTE_ID_CLAVE`): así el `titular_id` de M01 coincide con el de las pólizas de W03 y se
    pueden ligar. El número en claro nunca se guarda ni se loguea."""
    return hmac.new(
        clave.encode("utf-8"), numero.strip().encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
