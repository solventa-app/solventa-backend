"""Rotación de la clave de firma de los socios sin cortar tokens vivos (HA-SEG-004, ADR-14)."""

import base64

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.configuracion import SOCIOS_CLAVE_FIRMA_LOCAL, VALOR_MARCADOR, Configuracion
from app.tokens_socio import AUDIENCIA, EMISOR, FirmadorTokensSocio


def _clave_nueva() -> str:
    clave = ec.generate_private_key(ec.SECP256R1())
    der = clave.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.b64encode(der).decode()


def _validar(token: str, jwks: dict) -> dict:
    kid = jwt.get_unverified_header(token)["kid"]
    (jwk,) = [k for k in jwks["keys"] if k["kid"] == kid]
    return jwt.decode(
        token, jwt.PyJWK(jwk).key, algorithms=["ES256"], audience=AUDIENCIA, issuer=EMISOR
    )


def test_sin_clave_anterior_el_jwks_tiene_una_sola() -> None:
    assert len(FirmadorTokensSocio(SOCIOS_CLAVE_FIRMA_LOCAL).jwks()["keys"]) == 1


def test_durante_la_rotacion_validan_el_token_viejo_y_el_nuevo() -> None:
    vieja = FirmadorTokensSocio(SOCIOS_CLAVE_FIRMA_LOCAL)
    token_viejo = vieja.emitir("socio-1", "PRODUCCION")["token"]

    nueva = FirmadorTokensSocio(_clave_nueva(), clave_anterior=SOCIOS_CLAVE_FIRMA_LOCAL)
    token_nuevo = nueva.emitir("socio-1", "PRODUCCION")["token"]
    jwks = nueva.jwks()

    assert [k["kid"] for k in jwks["keys"]] == [nueva.kid, vieja.kid]
    assert jwt.get_unverified_header(token_nuevo)["kid"] == nueva.kid
    assert _validar(token_viejo, jwks)["sub"] == "socio-1"
    assert _validar(token_nuevo, jwks)["sub"] == "socio-1"
    assert all("d" not in k for k in jwks["keys"])


def test_la_anterior_igual_a_la_vigente_no_se_repite() -> None:
    local = SOCIOS_CLAVE_FIRMA_LOCAL
    firmador = FirmadorTokensSocio(local, clave_anterior=local)
    assert len(firmador.jwks()["keys"]) == 1


def test_una_clave_anterior_invalida_falla_al_arrancar() -> None:
    with pytest.raises(ValueError):
        FirmadorTokensSocio(SOCIOS_CLAVE_FIRMA_LOCAL, clave_anterior="no-es-una-clave")


@pytest.mark.parametrize("valor", ["", "  ", VALOR_MARCADOR])
def test_sin_rotacion_en_curso_no_hay_clave_anterior(monkeypatch, valor) -> None:
    monkeypatch.setenv("SOCIOS_CLAVE_FIRMA_ANTERIOR", valor)
    assert Configuracion.desde_entorno().socios_clave_firma_anterior is None


def test_la_clave_anterior_sale_del_entorno(monkeypatch) -> None:
    clave = _clave_nueva()
    monkeypatch.setenv("SOCIOS_CLAVE_FIRMA_ANTERIOR", clave)
    assert Configuracion.desde_entorno().socios_clave_firma_anterior == clave
