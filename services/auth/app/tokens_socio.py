"""Tokens de los socios de distribución (HU-W07, W07-4 y W07-26): JWT **ES256** de 15 minutos.

A diferencia de los usuarios (HS256 con el secreto que comparten AUTH y el BFF), el token del socio
lo valida también el API Gateway, y lo hace con la **clave pública** que AUTH publica como JWKS. Con
HS256 el gateway tendría que guardar el mismo secreto que firma los tokens de todos los roles.

La clave privada llega como `SOCIOS_CLAVE_FIRMA`: PKCS#8 en PEM o su DER en base64 (una línea, más
cómoda en variables de entorno). En local hay una fija, como el resto de secretos locales
(`configuracion.SOCIOS_CLAVE_FIRMA_LOCAL`); en Cloud Run sale de Secret Manager
(`clave-firma-socios`). Nunca se loguea el token."""

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ALGORITMO_SOCIOS = "ES256"
EMISOR = "solventa-auth"
AUDIENCIA = "solventa-socios"
VIGENCIA_MINUTOS = 15
SCOPES_SOCIO = ["cotizaciones", "propuestas", "polizas", "cuenta"]


def cargar_clave_privada(texto: str) -> ec.EllipticCurvePrivateKey:
    """PEM o DER en base64. Falla claro si no es una clave EC P-256."""
    texto = texto.strip()
    if texto.startswith("-----BEGIN"):
        clave = serialization.load_pem_private_key(texto.encode(), password=None)
    else:
        clave = serialization.load_der_private_key(base64.b64decode(texto), password=None)
    if not isinstance(clave, ec.EllipticCurvePrivateKey) or clave.curve.name != "secp256r1":
        raise ValueError("SOCIOS_CLAVE_FIRMA debe ser una clave EC P-256")
    return clave


def _kid(jwk: dict[str, Any]) -> str:
    """Huella de la clave pública (RFC 7638): cambia si se rota la clave."""
    canonico = json.dumps({k: jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":"))
    return (
        base64.urlsafe_b64encode(hashlib.sha256(canonico.encode()).digest()).rstrip(b"=").decode()
    )


class FirmadorTokensSocio:
    def __init__(
        self,
        clave_privada: str,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        vigencia_minutos: int = VIGENCIA_MINUTOS,
    ) -> None:
        self._clave = cargar_clave_privada(clave_privada)
        self._reloj = reloj
        self._vigencia = timedelta(minutes=vigencia_minutos)
        publica = jwt.algorithms.ECAlgorithm.to_jwk(self._clave.public_key(), as_dict=True)
        self._kid = _kid(publica)
        self._jwk = {**publica, "kid": self._kid, "alg": ALGORITMO_SOCIOS, "use": "sig"}

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        return {"keys": [self._jwk]}

    def emitir(self, socio_id: str, entorno: str) -> dict[str, Any]:
        ahora = self._reloj()
        expira_en = ahora + self._vigencia
        token = jwt.encode(
            {
                "iss": EMISOR,
                "aud": AUDIENCIA,
                "sub": socio_id,
                "rol": "SOCIO",
                "entorno": entorno,
                "scopes": SCOPES_SOCIO,
                "iat": int(ahora.timestamp()),
                "exp": int(expira_en.timestamp()),
                "jti": str(uuid.uuid4()),
            },
            self._clave,
            algorithm=ALGORITMO_SOCIOS,
            headers={"kid": self._kid},
        )
        return {"token": token, "expira_en": expira_en, "entorno": entorno, "socio_id": socio_id}
