"""El cifrado de los documentos firmados (KAN-26, T6, D-1): AES-256-GCM con una llave de datos por
documento, envuelta por KMS (o por la llave de desarrollo), con el dueño como dato asociado."""

import base64
import os
from typing import Any

import pytest

from app.application.cifrado import CIFRADO_CLAVE_LOCAL
from app.application.sobre_documentos import (
    MAGIA,
    EnvolvedorKms,
    EnvolvedorLocal,
    ErrorDeSobre,
    cifrar_documento,
    descifrar_documento,
    envolvedor_desde_entorno,
)

PDF = b"%PDF-1.4 contrato firmado de Ana Maria Perez, referencia fe-123 " + os.urandom(64)
PROPUESTA = "prop-1"
TIPO = "poliza-pdf"


@pytest.fixture
def envolvedor() -> EnvolvedorLocal:
    return EnvolvedorLocal.desde_base64(CIFRADO_CLAVE_LOCAL)


def test_ida_y_vuelta_y_el_contenedor_no_deja_ver_el_pdf(envolvedor) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)

    assert cifrado.contenedor.startswith(MAGIA)
    assert b"%PDF" not in cifrado.contenedor
    assert b"Ana Maria" not in cifrado.contenedor
    assert cifrado.tamano_bytes == len(PDF)
    assert (
        descifrar_documento(
            cifrado.contenedor, cifrado.clave_envuelta, PROPUESTA, TIPO, envolvedor, cifrado.sha256
        )
        == PDF
    )


def test_cada_documento_tiene_su_propia_llave_de_datos_y_su_propio_nonce(envolvedor) -> None:
    uno = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)
    otro = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)

    assert uno.clave_envuelta != otro.clave_envuelta
    assert uno.contenedor != otro.contenedor
    # La llave de datos no queda en claro en ningún lado.
    llave = envolvedor.desenvolver(
        base64.urlsafe_b64decode(uno.clave_envuelta), f"{PROPUESTA}:{TIPO}"
    )
    assert len(llave) == 32 and llave not in uno.contenedor


@pytest.mark.parametrize(("propuesta", "tipo"), [("prop-2", TIPO), (PROPUESTA, "contrato-firmado")])
def test_el_dueno_viaja_autenticado_copiar_a_otra_propuesta_o_tipo_falla(
    envolvedor, propuesta, tipo
) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)

    with pytest.raises(ErrorDeSobre):
        descifrar_documento(cifrado.contenedor, cifrado.clave_envuelta, propuesta, tipo, envolvedor)


def test_un_byte_alterado_se_detecta_por_la_etiqueta_gcm(envolvedor) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)
    alterado = bytearray(cifrado.contenedor)
    alterado[len(alterado) // 2] ^= 0x01

    with pytest.raises(ErrorDeSobre):
        descifrar_documento(bytes(alterado), cifrado.clave_envuelta, PROPUESTA, TIPO, envolvedor)


def test_la_etiqueta_truncada_o_el_contenedor_mal_formado_no_se_sirve(envolvedor) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)

    for malo in (cifrado.contenedor[:-5], b"", b"%PDF-1.4 en claro", MAGIA + b"\x09" + bytes(40)):
        with pytest.raises(ErrorDeSobre):
            descifrar_documento(malo, cifrado.clave_envuelta, PROPUESTA, TIPO, envolvedor)


def test_con_otra_llave_maestra_no_se_puede_desenvolver(envolvedor) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)
    intruso = EnvolvedorLocal(os.urandom(32))

    with pytest.raises(ErrorDeSobre):
        descifrar_documento(cifrado.contenedor, cifrado.clave_envuelta, PROPUESTA, TIPO, intruso)


def test_la_huella_guardada_debe_coincidir(envolvedor) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)

    with pytest.raises(ErrorDeSobre, match="huella"):
        descifrar_documento(
            cifrado.contenedor, cifrado.clave_envuelta, PROPUESTA, TIPO, envolvedor, "0" * 64
        )


def test_una_llave_envuelta_ilegible_es_una_falla_de_integridad(envolvedor) -> None:
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)

    with pytest.raises(ErrorDeSobre):
        descifrar_documento(cifrado.contenedor, "###", PROPUESTA, TIPO, envolvedor)


class KmsFalso:
    """Lo que `google.cloud.kms.KeyManagementServiceClient` hace con `encrypt` y `decrypt`: con el
    dato asociado, y un cambio de contexto hace fallar el descifrado."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str, bytes]] = []
        self.deshabilitado = False

    class _R:
        def __init__(self, **campos: bytes) -> None:
            self.__dict__.update(campos)

    def encrypt(self, request: dict[str, Any]) -> Any:
        self.llamadas.append(("encrypt", request["name"], request["additional_authenticated_data"]))
        envuelta = request["additional_authenticated_data"] + b"|" + request["plaintext"][::-1]
        return self._R(ciphertext=envuelta)

    def decrypt(self, request: dict[str, Any]) -> Any:
        self.llamadas.append(("decrypt", request["name"], request["additional_authenticated_data"]))
        if self.deshabilitado:
            raise RuntimeError("la versión de la llave está deshabilitada")
        aad, _, llave = request["ciphertext"].partition(b"|")
        if aad != request["additional_authenticated_data"]:
            raise RuntimeError("dato asociado distinto")
        return self._R(plaintext=llave[::-1])


def test_con_kms_la_llave_de_datos_se_envuelve_en_kms_con_el_dueno_como_dato_asociado() -> None:
    kms = KmsFalso()
    envolvedor = EnvolvedorKms("projects/p/locations/l/keyRings/k/cryptoKeys/campos", kms)

    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)
    pdf = descifrar_documento(
        cifrado.contenedor, cifrado.clave_envuelta, PROPUESTA, TIPO, envolvedor, cifrado.sha256
    )

    assert pdf == PDF
    assert [(op, aad) for op, _, aad in kms.llamadas] == [
        ("encrypt", b"prop-1:poliza-pdf"),
        ("decrypt", b"prop-1:poliza-pdf"),
    ]
    assert {nombre for _, nombre, _ in kms.llamadas} == {
        "projects/p/locations/l/keyRings/k/cryptoKeys/campos"
    }


def test_si_kms_deshabilita_la_llave_el_documento_no_se_puede_leer_y_no_es_integridad() -> None:
    """EC022: una falla de KMS sube tal cual (disponibilidad), no como `ErrorDeSobre`."""
    kms = KmsFalso()
    envolvedor = EnvolvedorKms("llave", kms)
    cifrado = cifrar_documento(PDF, PROPUESTA, TIPO, envolvedor)
    kms.deshabilitado = True

    with pytest.raises(RuntimeError, match="deshabilitada"):
        descifrar_documento(cifrado.contenedor, cifrado.clave_envuelta, PROPUESTA, TIPO, envolvedor)


def test_el_envolvedor_sale_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KMS_KEY_NAME", raising=False)
    assert isinstance(envolvedor_desde_entorno(), EnvolvedorLocal)
    monkeypatch.setenv("KMS_KEY_NAME", "projects/p/locations/l/keyRings/k/cryptoKeys/c")
    assert isinstance(envolvedor_desde_entorno(), EnvolvedorKms)
